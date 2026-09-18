/** Incremental SSE decoding: network chunks need not end on UTF-8 or line boundaries. */
export async function consumeEvents(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "", event = "message", data = [], terminal = null;
  function line(value) {
    if (!value) {
      if (data.length) {
        const body = JSON.parse(data.join("\n"));
        const record = { event, data: body };
        if (terminal) throw new Error("Unexpected event after terminal response");
        onEvent(record);
        if (event === "result" || event === "error") terminal = record;
      }
      event = "message"; data = [];
    } else if (value.startsWith("event:")) event = value.slice(6).replace(/^ /, "");
    else if (value.startsWith("data:")) data.push(value.slice(5).replace(/^ /, ""));
  }
  function drain(final = false) {
    while (buffer.length) {
      const match = /[\r\n]/.exec(buffer);
      if (!match) break;
      const i = match.index;
      if (buffer[i] === "\r" && i === buffer.length - 1 && !final) break;
      const width = buffer.slice(i, i + 2) === "\r\n" ? 2 : 1;
      line(buffer.slice(0, i));
      buffer = buffer.slice(i + width);
    }
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      drain();
    }
    buffer += decoder.decode(); drain(true);
    if (!terminal) throw new Error("Connection ended without a terminal result");
    return terminal;
  } finally {
    reader.releaseLock();
  }
}
