from pathlib import Path

from webchat.domain.interactions import single_action_interaction
from webchat.persistence.database import Database
from webchat.persistence.repositories import ConversationRepository, MessageRepository


def test_conversation_stores_hash_and_orders_messages(tmp_path: Path) -> None:
    database = Database(tmp_path / "webchat.sqlite3")
    database.migrate()
    conversations = ConversationRepository(database)
    messages = MessageRepository(database)
    token = "raw-secret-token"
    conversation = conversations.create(token, {"path": "/"}, 30)

    messages.add(conversation["id"], "user", "one", None)
    interaction = single_action_interaction(
        "Would you like to see services?",
        {"type": "show_workshop_services"},
    )
    messages.add(
        conversation["id"],
        "assistant",
        "two",
        None,
        interaction_json=interaction.as_json(),
    )

    assert conversations.authorize(conversation["id"], token)
    second = conversations.create(token, {"path": "/offers"}, 30)
    assert conversations.authorize(second["id"], token)
    assert [item["id"] for item in conversations.list_for_session(token)] == [
        second["id"],
        conversation["id"],
    ]
    assert conversations.get_contexts(conversation["id"])["current"] == {"path": "/"}
    conversations.update_context(conversation["id"], {"path": "/vehicles?vehicle=veh-019"})
    assert conversations.get_contexts(conversation["id"]) == {
        "initial": {"path": "/"},
        "current": {"path": "/vehicles?vehicle=veh-019"},
    }
    assert [message.text for message in messages.list(conversation["id"])] == ["one", "two"]
    assert messages.list(conversation["id"])[1].interaction_json == interaction.as_json()
    assert token.encode() not in database.path.read_bytes()
