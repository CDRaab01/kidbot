"""Tests for server/memory.py — durable fact extraction."""
from server.memory import extract_facts


class TestExtractFacts:
    def test_age(self):
        assert extract_facts("I'm 8 years old") == {"age": "they are 8 years old"}
        assert extract_facts("i am 7") == {"age": "they are 7 years old"}

    def test_implausible_age_ignored(self):
        assert "age" not in extract_facts("I have 99 stickers")

    def test_pet_with_name(self):
        f = extract_facts("I have a dog named Rex")
        assert f["pet"] == "they have a dog named Rex"

    def test_pet_without_name(self):
        f = extract_facts("I've got a cat")
        assert f["pet"] == "they have a cat"

    def test_fear(self):
        f = extract_facts("I'm scared of thunderstorms")
        assert f["fear"] == "they feel scared of thunderstorms"

    def test_favourite(self):
        f = extract_facts("my favourite dinosaur is the stegosaurus")
        assert f["favourite dinosaur"] == "their favourite dinosaur is the stegosaurus"

    def test_favorite_us_spelling(self):
        f = extract_facts("my favorite color is blue")
        assert f["favourite color"] == "their favourite color is blue"

    def test_nickname(self):
        f = extract_facts("you can call me Ace")
        assert f["name"] == "they sometimes go by Ace"

    def test_multiple_facts_in_one_utterance(self):
        f = extract_facts("I'm 9 and I have a hamster named Nibbles")
        assert f["age"] == "they are 9 years old"
        assert f["pet"] == "they have a hamster named Nibbles"

    def test_nothing_to_extract(self):
        assert extract_facts("tell me about volcanoes") == {}

    def test_unsafe_value_dropped(self):
        # "favourite ... is a knife" → sentence contains a blocked keyword → dropped.
        assert extract_facts("my favourite thing is a knife") == {}
