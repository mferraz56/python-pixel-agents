import type { ViewerMessage } from '../../shared/messages.ts';

function dispatch(message: ViewerMessage): void {
  window.dispatchEvent(new MessageEvent('message', { data: message }));
}

function getEventsUrl(): string {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token');
  if (!token) {
    throw new Error('Missing remote viewer token in URL');
  }
  return `/api/viewer/events?token=${encodeURIComponent(token)}`;
}

export function startRemoteViewerStream(): () => void {
  const source = new EventSource(getEventsUrl());

  source.addEventListener('bootstrap', (event) => {
    const bootstrapEvent = event as MessageEvent<string>;
    const messages = JSON.parse(bootstrapEvent.data) as ViewerMessage[];
    dispatch({ type: 'viewerReset' });
    for (const message of messages) {
      dispatch(message);
    }
  });

  source.addEventListener('message', (event) => {
    const liveEvent = event as MessageEvent<string>;
    dispatch(JSON.parse(liveEvent.data) as ViewerMessage);
  });

  source.onerror = () => {
    console.warn('[RemoteViewer] Event stream disconnected; waiting for SSE reconnect');
  };

  return () => source.close();
}