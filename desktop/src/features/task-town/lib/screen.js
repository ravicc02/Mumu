// Passive viewing uses a separate ticket; no pointer, keyboard or task actions are sent.
export function createCompanyScreenFeed(api, callbacks, Socket = globalThis.WebSocket) {
  let socket = null, generation = 0, selectedId = '', disposed = false;
  function disconnect() {
    generation++;
    if (socket) { socket.onmessage = socket.onclose = socket.onerror = null; socket.close(); socket = null; }
  }
  async function select(task, force = false) {
    const identity = task ? (task.task_kind || 'assignment') + ':' + task.id : '';
    if (disposed || (!force && identity === selectedId)) return;
    disconnect(); selectedId = identity;
    callbacks.status(task, task ? 'connecting' : 'idle');
    if (!task) return;
    const token = generation, kind = task.task_kind === 'learning' ? 'learning' : 'assignment';
    try {
      const ticket = await api.browserSession(kind, task.id);
      if (disposed || token !== generation) return;
      const current = new Socket(api.browserUrl(kind, task.id)); socket = current;
      current.onopen = () => { if (token === generation && !disposed) current.send(JSON.stringify(ticket)); };
      current.onmessage = event => {
        if (disposed || token !== generation) return;
        let message;
        try { message = JSON.parse(event.data); } catch { callbacks.status(task, 'disconnected'); disconnect(); return; }
        if (message.type === 'frame' && typeof message.image === 'string') callbacks.frame(message, task);
        else if (message.type === 'switching') callbacks.status(task, 'connecting');
        else if (message.type === 'closed') { callbacks.status(task, 'disconnected'); disconnect(); }
      };
      current.onclose = current.onerror = () => {
        if (token !== generation || disposed) return;
        callbacks.status(task, 'disconnected'); disconnect();
      };
    } catch { if (!disposed && token === generation) callbacks.status(task, 'disconnected'); }
  }
  return { select, dispose() { disposed = true; disconnect(); } };
}
