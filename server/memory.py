"""Lightweight long-term memory: pull durable facts a child shares out of their
own words so the bot can remember them across sessions and days.

Deterministic regex extraction (no extra LLM call, so no added latency on the
slow local model). Each fact is stored as a ready-to-read sentence keyed by a
stable category, so newer statements overwrite older ones (e.g. a new age).
"""
import re

from .guardrails import is_output_safe

_MAX_VALUE_LEN = 60

_PETS = (
    "dog", "puppy", "cat", "kitten", "fish", "hamster", "rabbit", "bunny",
    "bird", "parrot", "budgie", "guinea pig", "pony", "horse", "lizard",
    "snake", "turtle", "tortoise", "frog", "gecko",
)


def _clean(value: str) -> str:
    value = value.strip().strip(".!?,;:").strip()
    return value[:_MAX_VALUE_LEN].strip()


def extract_facts(text: str) -> dict[str, str]:
    """Return durable facts found in the child's utterance.

    Keys are stable categories (so updates overwrite); values are short,
    display-ready sentences for injection into the system prompt. Any value
    that would trip the output filter is dropped.
    """
    facts: dict[str, str] = {}
    t = text.strip()

    # Age: "I'm 8", "I am 8 years old"
    m = re.search(r"\bi(?:'?m| am)\s+(\d{1,2})\b(?:\s+years?\s+old)?", t, re.I)
    if m and 1 <= int(m.group(1)) <= 17:
        facts["age"] = f"they are {m.group(1)} years old"

    # Pet: "I have a dog named Rex" / "I've got a cat called Whiskers" / "I have a dog"
    pet_re = re.compile(
        r"\bi(?:'ve| have| have got| got)\b[^.!?]*?\b(" + "|".join(_PETS) + r")s?\b"
        r"(?:\s+(?:named|called)\s+([A-Za-z][A-Za-z'-]{0,14}))?",
        re.I,
    )
    m = pet_re.search(t)
    if m:
        animal = m.group(1).lower()
        if m.group(2):
            facts["pet"] = f"they have a {animal} named {m.group(2).capitalize()}"
        else:
            facts["pet"] = f"they have a {animal}"

    # Fear: "I'm scared of thunderstorms"
    m = re.search(
        r"\bi(?:'?m| am)\s+(?:scared|afraid|frightened|terrified)\s+of\s+(.+?)(?:[.!?]|$)",
        t, re.I,
    )
    if m:
        v = _clean(m.group(1))
        if v:
            facts["fear"] = f"they feel scared of {v}"

    # Favourite: "my favourite dinosaur is the stegosaurus"
    m = re.search(r"\bmy favou?rite\s+([a-z ]{2,20}?)\s+is\s+(.+?)(?:[.!?]|$)", t, re.I)
    if m:
        thing = _clean(m.group(1).lower())
        val = _clean(m.group(2))
        if thing and val:
            facts[f"favourite {thing}"] = f"their favourite {thing} is {val}"

    # Name / nickname: "my name is Alex", "call me Alex"
    m = re.search(r"\b(?:my name is|call me|i'?m called)\s+([A-Za-z][A-Za-z'-]{1,14})\b", t, re.I)
    if m:
        facts["name"] = f"they sometimes go by {m.group(1).capitalize()}"

    # Defensive: never store a fact whose sentence trips the output filter.
    return {k: v for k, v in facts.items() if is_output_safe(v)[0]}
