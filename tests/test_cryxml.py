import struct

import pytest

import cryxmlbuilder as B
from starcompanion.extract import cryxml
from starcompanion.extract.cryxml import CryXmlError, NotCryXmlError


def test_detects_the_magic():
    assert cryxml.is_cryxml(B.sample())
    assert not cryxml.is_cryxml(b"<?xml version='1.0'?><root/>")


def test_text_xml_is_refused_with_a_useful_message():
    """Plain XML is a different format, not a corrupt one."""
    with pytest.raises(NotCryXmlError, match="text XML"):
        cryxml.loads(b"<?xml version='1.0'?><root/>")


def test_parses_the_tree():
    root = cryxml.loads(B.sample())
    assert root.tag == "Subsumption"
    assert [c.tag for c in root.children] == ["Subactivity", "Variables"]


def test_attributes_decode():
    root = cryxml.loads(B.sample())
    activity = root.find("Subactivity")
    assert activity.get("ID") == "abc-123"
    assert activity.get("Name") == "Init"
    assert activity.get("Missing") is None
    assert activity.get("Missing", "fallback") == "fallback"


def test_nesting_is_preserved():
    root = cryxml.loads(B.sample())
    tasks = root.find("Subactivity").children
    assert [t.get("Type") for t in tasks] == ["Wait", "Spawn"]


def test_findall_searches_descendants():
    root = cryxml.loads(B.sample())
    assert len(root.findall("Task")) == 2


def test_tag_search_is_case_insensitive():
    root = cryxml.loads(B.sample())
    assert root.find("task") is not None
    assert root.find("TASK") is not None


def test_walk_visits_every_node_depth_first():
    root = cryxml.loads(B.sample())
    tags = [n.tag for n in root.walk()]
    assert tags == ["Subsumption", "Subactivity", "Task", "Task", "Variables"]


def test_find_returns_none_when_absent():
    assert cryxml.loads(B.sample()).find("Nonexistent") is None


def test_a_child_defined_before_its_parent_still_links():
    """Children are wired after every node exists, so table order cannot
    break the tree."""
    root = B.Element("Root")
    deep = B.Element("A")
    deep.add(B.Element("B", {"x": "1"}))
    root.add(deep)

    parsed = cryxml.loads(B.build(root))
    assert parsed.find("B").get("x") == "1"


def test_empty_document_is_rejected():
    root = B.Element("Root")
    data = bytearray(B.build(root))
    struct.pack_into("<I", data, len(B.MAGIC) + 8, 0)  # node_count
    with pytest.raises(CryXmlError, match="no nodes"):
        cryxml.loads(bytes(data))


def test_truncated_file_is_reported():
    data = B.sample()
    with pytest.raises(CryXmlError, match="past the end"):
        cryxml.loads(data[: len(data) // 2])


def test_absurd_node_count_is_rejected():
    data = bytearray(B.sample())
    struct.pack_into("<I", data, len(B.MAGIC) + 8, 99_000_000)
    with pytest.raises(CryXmlError, match="implausible"):
        cryxml.loads(bytes(data))


def test_file_too_small_for_a_header():
    with pytest.raises(CryXmlError, match="too small"):
        cryxml.loads(B.MAGIC + b"\x00\x00")


def test_unicode_survives():
    root = B.Element("Root", {"name": "Xi'an — Wikelo’s trade"})
    assert cryxml.loads(B.build(root)).get("name") == "Xi'an — Wikelo’s trade"
