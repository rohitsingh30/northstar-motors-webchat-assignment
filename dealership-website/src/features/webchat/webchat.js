/**
 * This first UI slice deliberately has no business-operation shortcuts: actions that create or
 * change dealership records will be added behind the server-side chat API in later commits.
 */
export function createWebchat() {
  const launcher = document.querySelector("#chat-launcher");
  const dialog = document.querySelector("#chat-dialog");
  const closeButton = document.querySelector("#chat-close");
  const form = document.querySelector("#chat-form");
  const input = document.querySelector("#chat-input");
  const messages = document.querySelector("#chat-messages");

  function addMessage(author, text) {
    const item = document.createElement("li");
    item.className = `chat-message chat-message--${author}`;
    item.textContent = text;
    messages.append(item);
    item.scrollIntoView({ block: "nearest" });
  }

  function pageContext() {
    const vehicleId = new URL(window.location).searchParams.get("vehicle");
    return /^veh-\d{3}$/.test(vehicleId || "") ? vehicleId : null;
  }

  function open() {
    dialog.showModal();
    if (!messages.children.length) {
      const vehicleId = pageContext();
      addMessage(
        "assistant",
        vehicleId
          ? `I can help with the vehicle you are viewing (${vehicleId}). What would you like to know?`
          : "I can help you find a vehicle, understand offers, or find a dealership.",
      );
    }
    input.focus();
  }

  launcher.addEventListener("click", open);
  closeButton.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => launcher.focus());

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;

    addMessage("user", text);
    input.value = "";
    // The server API replaces this safe holding reply in the next implementation slice.
    addMessage("assistant", "Thanks — chat responses are being connected. You can browse our current stock while we finish this service.");
  });
}
