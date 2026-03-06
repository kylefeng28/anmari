#!/usr/bin/env python3
"""
Comprehensive test for Rust cache backend (cache_rs).
This tests all the methods to ensure API compatibility with cache.py
"""

import sys
from datetime import datetime

try:
    from cache_rs import EmailCache, CachedMessage
    print("✓ Successfully imported Rust cache backend (cache_rs)")
except ImportError as e:
    print(f"✗ Failed to import cache_rs: {e}")
    print("\nMake sure you've built the extension with maturin")
    sys.exit(1)

def test_basic_operations():
    """Test basic cache operations"""
    print("\n=== Testing Basic Operations ===")

    # Create cache instance
    cache = EmailCache.from_path("/tmp/test_basic.db", 90)
    print("✓ Created EmailCache instance")

    # Insert a message
    cache.insert_message(
        uid=1,
        folder="INBOX",
        from_addr="alice@example.com",
        from_name="Alice Smith",
        subject="Test Email 1",
        date="2024-03-01 10:00:00",
        flags=["\\Seen", "\\Flagged"]
    )
    print("✓ Inserted message 1")

    # Insert another message
    cache.insert_message(
        uid=2,
        folder="INBOX",
        from_addr="bob@example.com",
        from_name=None,
        subject="Test Email 2",
        date="2024-03-02 11:00:00",
        flags=[]
    )
    print("✓ Inserted message 2")

    # Get message
    msg = cache.get_message(1, "INBOX")
    assert msg is not None, "Message should exist"
    assert msg.uid == 1
    assert msg.folder == "INBOX"
    assert msg.from_addr == "alice@example.com"
    assert msg.from_name == "Alice Smith"
    assert msg.subject == "Test Email 1"
    print(f"✓ Retrieved message: {msg.subject}")
    print(f"  From: {msg.from_addr} ({msg.from_name})")
    print(f"  Flags: {msg.get_flags_as_list()}")

    # Update flags
    cache.update_flags(1, "INBOX", ["\\Seen"])
    msg = cache.get_message(1, "INBOX")
    assert msg.get_flags_as_list() == ["\\Seen"]
    print("✓ Updated flags")

    return cache

def test_folder_operations(cache):
    """Test folder state operations"""
    print("\n=== Testing Folder Operations ===")

    # Set folder state
    cache.set_folder_state("INBOX", 12345, 67890)
    print("✓ Set folder state")

    # Get folder state
    state = cache.get_folder_state("INBOX")
    assert state is not None
    (uidvalidity, highestmodseq) = state
    assert uidvalidity == 12345
    assert highestmodseq == 67890
    print(f"✓ Retrieved folder state: uidvalidity={uidvalidity}, highestmodseq={highestmodseq}")

    # Get last seen UID
    last_uid = cache.get_last_seen_uid("INBOX")
    assert last_uid == 2
    print(f"✓ Last seen UID: {last_uid}")

    # Get all UIDs
    uids = cache.get_all_uids("INBOX")
    assert len(uids) == 2
    assert 1 in uids and 2 in uids
    print(f"✓ All UIDs: {uids}")

def test_tag_operations(cache):
    """Test tag operations"""
    print("\n=== Testing Tag Operations ===")

    # Add tags
    cache.add_tag(1, "INBOX", "important")
    cache.add_tag(1, "INBOX", "work")
    cache.add_tag(2, "INBOX", "newsletter")
    print("✓ Added tags")

    # Get tags
    tags = cache.get_tags(1, "INBOX")
    assert "important" in tags
    assert "work" in tags
    print(f"✓ Tags for message 1: {tags}")

    # Remove tag
    cache.remove_tag(1, "INBOX", "work")
    tags = cache.get_tags(1, "INBOX")
    assert "work" not in tags
    assert "important" in tags
    print(f"✓ Removed tag, remaining: {tags}")

    # Tag multiple messages
    msg1 = cache.get_message(1, "INBOX")
    msg2 = cache.get_message(2, "INBOX")
    count = cache.tag_messages(
        [msg1, msg2],
        tags_to_add=["archived"],
        tags_to_remove=["important"]
    )
    assert count == 2
    print(f"✓ Tagged {count} messages")

    tags1 = cache.get_tags(1, "INBOX")
    tags2 = cache.get_tags(2, "INBOX")
    assert "archived" in tags1 and "archived" in tags2
    assert "important" not in tags1
    print(f"  Message 1 tags: {tags1}")
    print(f"  Message 2 tags: {tags2}")

def test_gmail_labels(cache):
    """Test Gmail label operations"""
    print("\n=== Testing Gmail Labels ===")

    # Set labels
    cache.set_gm_labels(1, "INBOX", ["INBOX", "IMPORTANT", "CATEGORY_PERSONAL"])
    print("✓ Set Gmail labels")

    # Get labels
    labels = cache.get_gm_labels(1, "INBOX")
    assert "INBOX" in labels
    assert "IMPORTANT" in labels
    assert "CATEGORY_PERSONAL" in labels
    print(f"✓ Gmail labels: {labels}")

    # Update labels
    cache.set_gm_labels(1, "INBOX", ["INBOX", "STARRED"])
    labels = cache.get_gm_labels(1, "INBOX")
    assert "STARRED" in labels
    assert "IMPORTANT" not in labels
    print(f"✓ Updated labels: {labels}")

def test_copy_message(cache):
    """Test message copy operation"""
    print("\n=== Testing Copy Message ===")

    # Copy message to another folder
    cache.copy_message(1, "INBOX", 100, "Archive")
    print("✓ Copied message from INBOX to Archive")

    # Verify copied message
    copied = cache.get_message(100, "Archive")
    assert copied is not None
    assert copied.uid == 100
    assert copied.folder == "Archive"
    assert copied.subject == "Test Email 1"
    print(f"✓ Verified copied message: {copied.subject}")

    # Verify tags were copied
    tags = cache.get_tags(100, "Archive")
    assert "archived" in tags
    print(f"✓ Tags copied: {tags}")

    # Verify labels were copied
    labels = cache.get_gm_labels(100, "Archive")
    assert len(labels) > 0
    print(f"✓ Labels copied: {labels}")

def test_search(cache):
    """Test search functionality"""
    print("\n=== Testing Search ===")

    # Simple search
    results = cache.search("INBOX", "Test")
    assert len(results) > 0
    print(f"✓ Search for 'Test' found {len(results)} messages")

    # Search by sender
    results = cache.search("INBOX", "alice")
    assert len(results) >= 1
    assert any(msg.from_addr == "alice@example.com" for msg in results)
    print(f"✓ Search for 'alice' found {len(results)} messages")

def test_delete_operations(cache):
    """Test delete operations"""
    print("\n=== Testing Delete Operations ===")

    # Delete a message
    cache.delete_message(2, "INBOX")
    msg = cache.get_message(2, "INBOX")
    assert msg is None
    print("✓ Deleted message 2")

    # Clear folder for UIDVALIDITY change
    cache.clear_folder_messages_for_uidvalidity_change("Archive")
    msg = cache.get_message(100, "Archive")
    assert msg is None
    state = cache.get_folder_state("Archive")
    assert state is None
    print("✓ Cleared folder for UIDVALIDITY change")

    # Clear folder state for cleanup
    cache.clear_folders_state_for_cache_cleanup(["INBOX"])
    state = cache.get_folder_state("INBOX")
    assert state is None
    print("✓ Cleared folder state for cleanup")

def test_edge_cases():
    """Test edge cases and error handling"""
    print("\n=== Testing Edge Cases ===")

    cache = EmailCache.from_path("/tmp/test_edge_cases.db", 30)

    # Get non-existent message
    msg = cache.get_message(999, "INBOX")
    assert msg is None
    print("✓ Non-existent message returns None")

    # Get folder state for non-existent folder
    state = cache.get_folder_state("NonExistent")
    assert state is None
    print("✓ Non-existent folder state returns None")

    # Empty flags
    cache.insert_message(
        uid=1,
        folder="INBOX",
        from_addr="test@example.com",
        from_name=None,
        subject="No flags",
        date="2024-03-01 10:00:00",
        flags=[]
    )
    msg = cache.get_message(1, "INBOX")
    assert msg.get_flags_as_list() == []
    print("✓ Empty flags handled correctly")

    # Get tags for message with no tags
    tags = cache.get_tags(1, "INBOX")
    assert tags == []
    print("✓ No tags returns empty list")

def main():
    print("=" * 60)
    print("Testing Rust Cache Backend (cache_rs)")
    print("=" * 60)

    try:
        cache = test_basic_operations()
        test_folder_operations(cache)
        test_tag_operations(cache)
        test_gmail_labels(cache)
        test_copy_message(cache)
        test_search(cache)
        test_delete_operations(cache)
        test_edge_cases()

        print("✓ ALL TESTS PASSED!")

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
