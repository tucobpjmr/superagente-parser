"""Test per sanitize_filename in main.py."""

from main import sanitize_filename


def test_sanitize_strips_posix_path():
    assert sanitize_filename("/etc/passwd") == "passwd"
    assert sanitize_filename("../../etc/passwd") == "passwd"


def test_sanitize_strips_windows_path():
    assert sanitize_filename("C:\\Windows\\System32\\evil.exe") == "evil.exe"
    assert sanitize_filename("..\\..\\file.txt") == "file.txt"


def test_sanitize_removes_control_chars():
    assert sanitize_filename("doc\x00ument.pdf") == "document.pdf"
    assert sanitize_filename("file\nname.txt") == "file_name.txt" or sanitize_filename("file\nname.txt") == "filename.txt"


def test_sanitize_replaces_unsafe_chars():
    result = sanitize_filename("my<file>name?.pdf")
    assert "<" not in result and ">" not in result and "?" not in result
    assert result.endswith(".pdf")


def test_sanitize_handles_empty():
    assert sanitize_filename("") == "unnamed"
    assert sanitize_filename(None) == "unnamed"
    assert sanitize_filename("   ") == "unnamed"


def test_sanitize_preserves_safe_chars():
    assert sanitize_filename("my-document_v2.pdf") == "my-document_v2.pdf"


def test_sanitize_truncates_long_name_preserving_ext():
    long_name = "a" * 300 + ".pdf"
    result = sanitize_filename(long_name)
    assert len(result) <= 200
    assert result.endswith(".pdf")


def test_sanitize_unicode_normalization():
    # Caratteri accentati sono OK
    result = sanitize_filename("documento_città.pdf")
    assert result.endswith(".pdf")
    assert "citt" in result
