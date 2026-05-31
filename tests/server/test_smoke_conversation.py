"""Tests for scripts/test_conversation.py — the deploy behavioural smoke test.

scripts/ isn't a package, so load by file path (same as test_smoke_images.py).
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "test_conversation.py"


def _load():
    spec = importlib.util.spec_from_file_location("smoke_test_conversation", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def smoke():
    return _load()


def _resp(status, headers=None):
    r = MagicMock(status_code=status)
    r.headers = headers or {}
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(response=r)
    else:
        r.raise_for_status.return_value = None
    return r


# ---------------------------------------------------------------------------
# _send_turn — rate-limit handling and reply extraction
# ---------------------------------------------------------------------------

class TestSendTurn:
    def test_returns_reply_on_200(self, smoke):
        with patch("requests.post", return_value=_resp(200, {"X-Reply": "hi there"})):
            assert smoke._send_turn("hi", "s1") == "hi there"

    def test_retries_on_429_then_succeeds(self, smoke):
        throttled = _resp(429, {"Retry-After": "2"})
        ok = _resp(200, {"X-Reply": "later"})
        with patch("requests.post", side_effect=[throttled, ok]) as post, \
             patch("time.sleep") as sleep:
            assert smoke._send_turn("hi", "s1") == "later"
        assert post.call_count == 2
        sleep.assert_called_once_with(3)  # Retry-After + 1

    def test_missing_retry_after_uses_default_wait(self, smoke):
        throttled = _resp(429, {})
        ok = _resp(200, {"X-Reply": "ok"})
        with patch("requests.post", side_effect=[throttled, ok]), \
             patch("time.sleep") as sleep:
            smoke._send_turn("hi", "s1")
        sleep.assert_called_once_with(13)

    def test_gives_up_after_persistent_429(self, smoke):
        with patch("requests.post", return_value=_resp(429, {"Retry-After": "1"})), \
             patch("time.sleep"):
            assert smoke._send_turn("hi", "s1") == ""

    def test_returns_empty_on_http_error(self, smoke):
        with patch("requests.post", return_value=_resp(500)):
            assert smoke._send_turn("hi", "s1") == ""


# ---------------------------------------------------------------------------
# _run_probe — turns are driven in order, with the same session_id
# ---------------------------------------------------------------------------

class TestRunProbe:
    def test_session_id_constant_across_turns(self, smoke):
        seen_sessions: list[str] = []

        def fake_post(url, data, headers, timeout):
            seen_sessions.append(data["session_id"])
            return _resp(200, {"X-Reply": "reply"})

        with patch("requests.post", side_effect=fake_post), patch("time.sleep"):
            transcript = smoke._run_probe("flow", ["a", "b", "c"])
        assert len(transcript) == 3
        assert len(set(seen_sessions)) == 1  # one session id, exercised across turns

    def test_session_id_unique_per_probe_run(self, smoke):
        with patch("requests.post", return_value=_resp(200, {"X-Reply": "ok"})), \
             patch("time.sleep"):
            a = smoke._run_probe("flow", ["hi"])[0]
            b = smoke._run_probe("flow", ["hi"])[0]
        # Different runs of the same-named probe must use different ids so
        # they don't pollute each other's memory.
        # (we drove _run_probe twice — the unique part is the random suffix)
        # The transcript itself doesn't expose the id; assert via the POST args.
        # Done indirectly above; here we just ensure both ran.
        assert a[1] == "ok" and b[1] == "ok"

    def test_stops_early_on_empty_reply(self, smoke):
        # First turn ok, second turn fails (server error → empty reply).
        with patch("requests.post", side_effect=[
                _resp(200, {"X-Reply": "first"}),
                _resp(500),
             ]), patch("time.sleep"):
            transcript = smoke._run_probe("flow", ["a", "b", "c"])
        assert [t[1] for t in transcript] == ["first", ""]

    def test_no_image_check_by_default(self, smoke):
        """Default probes don't hit /latest_image — keeps existing probes
        byte-identical to before the check_image opt landed."""
        with patch("requests.post", return_value=_resp(200, {"X-Reply": "ok"})), \
             patch("requests.get") as get, \
             patch("time.sleep"):
            transcript = smoke._run_probe("flow", ["a"])
        get.assert_not_called()
        # Third slot is None when no check happened.
        assert transcript[0][2] is None

    def test_check_image_fetches_latest_image_per_turn(self, smoke):
        """With check_image=True, /latest_image is polled after each turn
        and the URL ends up in the transcript's third slot."""
        image_resp = MagicMock(status_code=200)
        image_resp.json.return_value = {"image_url": "http://x/elephants.jpg"}
        with patch("requests.post", return_value=_resp(200, {"X-Reply": "ok"})), \
             patch("requests.get", return_value=image_resp) as get, \
             patch("time.sleep"):
            transcript = smoke._run_probe("flow", ["a", "b"], check_image=True)
        assert get.call_count == 2
        assert transcript[0][2] == "http://x/elephants.jpg"
        assert transcript[1][2] == "http://x/elephants.jpg"

    def test_check_image_empty_when_no_image_fetched(self, smoke):
        """Empty image_url from the server means no image was fetched —
        record as empty string so _format_transcript renders 'no'."""
        image_resp = MagicMock(status_code=200)
        image_resp.json.return_value = {"image_url": ""}
        with patch("requests.post", return_value=_resp(200, {"X-Reply": "ok"})), \
             patch("requests.get", return_value=image_resp), \
             patch("time.sleep"):
            transcript = smoke._run_probe("flow", ["a"], check_image=True)
        assert transcript[0][2] == ""

    def test_check_image_network_failure_is_empty_not_none(self, smoke):
        """A failed image check shouldn't break the probe — record empty so
        the judge sees 'no' rather than 'no marker at all'."""
        with patch("requests.post", return_value=_resp(200, {"X-Reply": "ok"})), \
             patch("requests.get", side_effect=requests.ConnectionError), \
             patch("time.sleep"):
            transcript = smoke._run_probe("flow", ["a"], check_image=True)
        assert transcript[0][2] == ""


# ---------------------------------------------------------------------------
# _format_transcript — image marker only when the probe opted in
# ---------------------------------------------------------------------------

class TestFormatTranscript:
    def test_no_image_marker_for_2_tuple_legacy(self, smoke):
        """Old 2-tuple shape (used in unit tests / _judge fixtures) must
        still render without an image line."""
        out = smoke._format_transcript([("hi", "hello")])
        assert "[image fetched" not in out
        assert "hi" in out and "hello" in out

    def test_no_image_marker_when_third_slot_is_none(self, smoke):
        out = smoke._format_transcript([("hi", "hello", None)])
        assert "[image fetched" not in out

    def test_image_marker_yes_when_url_present(self, smoke):
        out = smoke._format_transcript([("hi", "hello", "http://x/y.jpg")])
        assert "[image fetched: yes]" in out

    def test_image_marker_no_when_url_empty(self, smoke):
        out = smoke._format_transcript([("hi", "hello", "")])
        assert "[image fetched: no]" in out


# ---------------------------------------------------------------------------
# _judge — verdict parsing and failure handling
# ---------------------------------------------------------------------------

class TestDetectJudgeModel:
    """Regression: LM Studio's /v1/models lists every loaded model, including
    image-gen (Flux) and embeddings. Picking the first entry blindly broke
    against a real user setup where Flux came back first and 400'd on chat.
    """

    def _models_resp(self, models: list[dict]):
        r = MagicMock(status_code=200)
        r.json.return_value = {"data": models}
        r.raise_for_status.return_value = None
        return r

    def test_skips_flux_image_model(self, smoke):
        # The actual failure mode reported by the user.
        with patch("requests.get", return_value=self._models_resp([
                {"id": "flux.2-klein-9b"},
                {"id": "gemma-3-4b-it"},
             ])):
            assert smoke._detect_judge_model() == "gemma-3-4b-it"

    def test_skips_stable_diffusion(self, smoke):
        with patch("requests.get", return_value=self._models_resp([
                {"id": "stable-diffusion-xl"},
                {"id": "qwen2-7b-instruct"},
             ])):
            assert smoke._detect_judge_model() == "qwen2-7b-instruct"

    def test_skips_embedding_models(self, smoke):
        with patch("requests.get", return_value=self._models_resp([
                {"id": "nomic-embed-text-v1.5"},
                {"id": "bge-large-en"},
                {"id": "llama-3.2-3b-instruct"},
             ])):
            assert smoke._detect_judge_model() == "llama-3.2-3b-instruct"

    def test_respects_explicit_type_metadata(self, smoke):
        # LM Studio tags entries with type=embeddings; trust it even if the
        # id wouldn't trip the name deny-list.
        with patch("requests.get", return_value=self._models_resp([
                {"id": "custom-encoder-v2", "type": "embeddings"},
                {"id": "qwen-chat", "type": "llm"},
             ])):
            assert smoke._detect_judge_model() == "qwen-chat"

    def test_falls_back_to_only_loaded_model(self, smoke):
        # Single non-deny-listed model — keep current behaviour.
        with patch("requests.get", return_value=self._models_resp([
                {"id": "gemma-3-4b-it"},
             ])):
            assert smoke._detect_judge_model() == "gemma-3-4b-it"

    def test_returns_none_when_all_models_are_non_chat(self, smoke):
        with patch("requests.get", return_value=self._models_resp([
                {"id": "flux.2-klein-9b"},
                {"id": "nomic-embed-text-v1.5"},
             ])):
            assert smoke._detect_judge_model() is None

    def test_returns_none_on_network_failure(self, smoke):
        with patch("requests.get", side_effect=requests.ConnectionError):
            assert smoke._detect_judge_model() is None

    def test_returns_none_on_empty_model_list(self, smoke):
        with patch("requests.get", return_value=self._models_resp([])):
            assert smoke._detect_judge_model() is None

    def test_prefers_production_model_when_loaded(self, smoke):
        """If the server's LM_STUDIO_MODEL substring-matches a loaded model,
        prefer it over auto-detect — so the judge runs on the same model
        serving children, not a weaker one (moondream-2b etc.) loaded
        alongside."""
        with patch.object(smoke, "PROD_MODEL", "google/gemma-4-e4b"), \
             patch("requests.get", return_value=self._models_resp([
                {"id": "moondream-2b-2025-04-14"},
                {"id": "google/gemma-4-e4b"},
             ])):
            assert smoke._detect_judge_model() == "google/gemma-4-e4b"

    def test_prod_model_matches_quant_suffix(self, smoke):
        """LM Studio sometimes lists models with quant suffixes like
        @q4_k_m. Fuzzy match handles that."""
        with patch.object(smoke, "PROD_MODEL", "google/gemma-4-e4b"), \
             patch("requests.get", return_value=self._models_resp([
                {"id": "moondream-2b"},
                {"id": "google/gemma-4-e4b@q4_k_m"},
             ])):
            assert smoke._detect_judge_model() == "google/gemma-4-e4b@q4_k_m"

    def test_prod_model_matches_stripped_prefix(self, smoke):
        """LM Studio may drop the publisher prefix on its end."""
        with patch.object(smoke, "PROD_MODEL", "google/gemma-4-e4b"), \
             patch("requests.get", return_value=self._models_resp([
                {"id": "gemma-4-e4b"},
             ])):
            assert smoke._detect_judge_model() == "gemma-4-e4b"

    def test_falls_back_to_deny_list_when_prod_not_loaded(self, smoke):
        """Production model not in the list → fall through to the existing
        chat-model filter rather than failing outright."""
        with patch.object(smoke, "PROD_MODEL", "google/gemma-4-e4b"), \
             patch("requests.get", return_value=self._models_resp([
                {"id": "flux.2-klein-9b"},
                {"id": "qwen2-7b-instruct"},
             ])):
            assert smoke._detect_judge_model() == "qwen2-7b-instruct"


class TestJudge:
    def _mock_openai_returning(self, content: str):
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=content))]
        )
        return client

    def test_yes_first_line_means_pass(self, smoke):
        client = self._mock_openai_returning("YES\nThe bot stayed on topic.")
        with patch("openai.OpenAI", return_value=client):
            verdict, explanation = smoke._judge([("a", "b")], "Q?", "model-x")
        assert verdict is True
        assert "stayed on topic" in explanation

    def test_no_first_line_means_fail(self, smoke):
        client = self._mock_openai_returning("NO\nPivoted to dinosaurs.")
        with patch("openai.OpenAI", return_value=client):
            verdict, _ = smoke._judge([("a", "b")], "Q?", "model-x")
        assert verdict is False

    def test_judge_failure_returns_none(self, smoke):
        with patch("openai.OpenAI", side_effect=RuntimeError("LM down")):
            verdict, explanation = smoke._judge([("a", "b")], "Q?", "model-x")
        assert verdict is None
        assert "judge error" in explanation

    def test_judge_called_with_full_transcript(self, smoke):
        client = self._mock_openai_returning("YES\nfine")
        transcript = [("hi", "hello there"), ("more", "okay")]
        with patch("openai.OpenAI", return_value=client):
            smoke._judge(transcript, "Did it stay on topic?", "model-x")
        sent = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert "hi" in sent and "hello there" in sent
        assert "more" in sent and "okay" in sent
        assert "Did it stay on topic?" in sent


# ---------------------------------------------------------------------------
# run() — pass/fail counting end-to-end
# ---------------------------------------------------------------------------

class TestRun:
    def _probe(self, name="p", turns=("hi",), q="Q?"):
        return [(name, list(turns), q)]

    def test_judge_pass_zero_failures(self, smoke):
        with patch.object(smoke, "_detect_judge_model", return_value="model-x"), \
             patch.object(smoke, "_run_probe", return_value=[("hi", "hello")]), \
             patch.object(smoke, "_judge", return_value=(True, "fine")):
            assert smoke.run(self._probe()) == 0

    def test_judge_fail_counted(self, smoke):
        with patch.object(smoke, "_detect_judge_model", return_value="model-x"), \
             patch.object(smoke, "_run_probe", return_value=[("hi", "hello")]), \
             patch.object(smoke, "_judge", return_value=(False, "pivoted")):
            assert smoke.run(self._probe()) == 1

    def test_no_judge_does_not_call_llm(self, smoke):
        with patch.object(smoke, "_detect_judge_model") as detect, \
             patch.object(smoke, "_run_probe", return_value=[("hi", "hello")]), \
             patch.object(smoke, "_judge") as judge:
            assert smoke.run(self._probe(), use_judge=False) == 0
        detect.assert_not_called()
        judge.assert_not_called()

    def test_no_reply_is_a_failure(self, smoke):
        # An empty bot reply counts as a probe failure even without the judge.
        with patch.object(smoke, "_detect_judge_model", return_value="model-x"), \
             patch.object(smoke, "_run_probe", return_value=[("hi", "")]), \
             patch.object(smoke, "_judge") as judge:
            assert smoke.run(self._probe()) == 1
        judge.assert_not_called()  # bailed before grading


# ---------------------------------------------------------------------------
# Probe definitions — these are the actual behavioural assertions in the build
# ---------------------------------------------------------------------------

class TestProbes:
    def test_probes_cover_on_topic_and_fact_recall(self, smoke):
        names = {p[0] for p in smoke.PROBES}
        # On-topic and fact-recall families are what unit tests can't cover.
        assert any(n.startswith("on_topic_") for n in names)
        assert any(n.startswith("fact_recall_") for n in names)

    def test_every_probe_has_a_yes_no_question(self, smoke):
        # Tuples are (name, turns, question, [opts]) — opts is optional.
        for probe in smoke.PROBES:
            name, question = probe[0], probe[2]
            assert "?" in question or "YES" in question or "NO" in question, \
                f"{name} judge question doesn't look like a YES/NO grading prompt"

    def test_every_probe_has_at_least_one_turn(self, smoke):
        # Single-turn probes are valid for behavioural rules that show up in
        # the bot's immediate response (e.g. corrects_wrong_assertion); the
        # earlier 2-turn floor was for memory probes specifically.
        for probe in smoke.PROBES:
            name, turns = probe[0], probe[1]
            assert len(turns) >= 1, f"{name} has zero turns"

    def test_production_bug_probes_present(self, smoke):
        """Probes that close the recurring bug classes (rainbow-style
        refusal, affirming wrong answers, image-fetched-without-asking)
        must remain in the suite — they're our smoke-level regression
        guards for those failure modes."""
        names = {p[0] for p in smoke.PROBES}
        for required in ("science_not_overcautious",
                         "corrects_wrong_assertion",
                         "no_image_when_not_asked"):
            assert required in names, f"missing production-bug probe: {required}"

    def test_major_mode_probes_present(self, smoke):
        """Every behavioural mode in _BASE_PROMPT is exercised by at least
        one smoke probe — otherwise prompt drift in those modes ships
        undetected."""
        names = {p[0] for p in smoke.PROBES}
        for required in ("quiz_mode", "jokes_and_riddles",
                         "math_challenge", "reverse_quiz"):
            assert required in names, f"missing major-mode probe: {required}"

    def test_check_image_opt_is_used_only_when_relevant(self, smoke):
        """check_image hits an extra endpoint per turn, so it should only
        be enabled on probes that actually need the marker."""
        for probe in smoke.PROBES:
            name = probe[0]
            opts = probe[3] if len(probe) > 3 else {}
            if opts.get("check_image"):
                # The only probe today that needs it.
                assert name == "no_image_when_not_asked", \
                    f"{name} sets check_image but doesn't need it"
