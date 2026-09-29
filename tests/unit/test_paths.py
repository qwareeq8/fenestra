"""Tests for Explorer location resolution and path canonicalization."""

from types import SimpleNamespace

from fenestra.platform.paths import canonicalize_path, resolve_explorer_location


def test_empty_string():
    """Empty string should return empty string."""
    assert canonicalize_path("") == ""


def test_forward_slashes_normalized():
    """Forward slashes should be converted to backslashes."""
    assert canonicalize_path("C:/Users/test") == "c:\\users\\test"


def test_trailing_separator_stripped():
    """Trailing backslash should be stripped (unless root drive)."""
    assert canonicalize_path("C:\\Users\\test\\") == "c:\\users\\test"


def test_root_drive_preserved():
    """Root drive path should keep its trailing backslash."""
    assert canonicalize_path("C:\\") == "c:\\"


def test_file_url_decoded():
    """file:/// URLs should be decoded and normalized."""
    assert canonicalize_path("file:///C:/Users/test%20dir") == "c:\\users\\test dir"


def test_unc_file_url_preserves_network_root():
    """A UNC file URL should retain both leading network separators."""
    assert canonicalize_path("file://nas-server/shared/My%20Files/") == (
        r"\\nas-server\shared\my files"
    )


def test_unc_path_and_file_url_match():
    """Equivalent UNC paths and file URLs should share one canonical form."""
    assert canonicalize_path(r"\\NAS-SERVER\Shared\Folder") == canonicalize_path(
        "file://nas-server/Shared/Folder"
    )


def test_canonicalization_is_idempotent_for_encoded_percent_sequences():
    """A decoded percent sign must not be decoded a second time as a path."""
    once = canonicalize_path("file:///C:/Folder/%2520")

    assert once == r"c:\folder\%20"
    assert canonicalize_path(once) == once


def test_case_normalized():
    """Path should be lowercased for case-insensitive comparison."""
    assert canonicalize_path("C:\\USERS\\TEST") == "c:\\users\\test"


def test_multiple_trailing_separators():
    """Multiple trailing backslashes should all be stripped."""
    assert canonicalize_path("C:\\Users\\test\\\\") == "c:\\users\\test"


def test_location_resolver_uses_virtual_folder_path_fallback():
    """Virtual folders with no URL should use Document.Folder.Self.Path."""
    window = SimpleNamespace(
        LocationURL="",
        Document=SimpleNamespace(
            Folder=SimpleNamespace(Self=SimpleNamespace(Path="::{CONTROL-PANEL}"))
        ),
        LocationName="Control Panel",
    )

    assert resolve_explorer_location(window) == "::{CONTROL-PANEL}"


def test_location_resolver_uses_display_name_as_last_com_fallback():
    """A display name should identify virtual tabs lacking a shell path."""
    window = SimpleNamespace(LocationURL="", Document=None, LocationName="Control Panel")

    assert resolve_explorer_location(window) == "Control Panel"
