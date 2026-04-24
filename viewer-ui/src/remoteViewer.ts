import type { ViewerMessage } from '../shared/messages.ts';
import { dispatchMockMessages } from './browserMock.ts';

function dispatch(message: ViewerMessage): void {
  window.dispatchEvent(new MessageEvent('message', { data: message }));
}

function getToken(): string {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token');
  if (!token) {
    throw new Error('Missing remote viewer token in URL');
  }
  return token;
}

function getEventsUrl(): string {
  return `/api/viewer/events?token=${encodeURIComponent(getToken())}`;
}

export interface ReplaySessionRef {
  providerId: string;
  sessionId: string;
}

export async function listReplaySessions(): Promise<ReplaySessionRef[]> {
  const r = await fetch(`/api/viewer/sessions?token=${encodeURIComponent(getToken())}`);
  if (!r.ok) {
    throw new Error(`sessions: HTTP ${r.status}`);
  }
  const body = (await r.json()) as { sessions: ReplaySessionRef[] };
  return body.sessions;
}

/**
 * Fetch a recorded session and replay it through the local view.
 * Resets the viewer first, re-dispatches sprites/layout, then the messages.
 */
export async function loadReplay(ref: ReplaySessionRef): Promise<number> {
  const url = `/api/viewer/replay/${encodeURIComponent(ref.providerId)}/${encodeURIComponent(
    ref.sessionId,
  )}?token=${encodeURIComponent(getToken())}`;
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`replay: HTTP ${r.status}`);
  }
  const body = (await r.json()) as { messages: ViewerMessage[]; envelopeCount: number };
  dispatch({ type: 'viewerReset' });
  dispatchMockMessages();
  for (const message of body.messages) {
    dispatch(message);
  }
  return body.envelopeCount;
}

export function startRemoteViewerStream(): () => void {
  const source = new EventSource(getEventsUrl());

  source.addEventListener('bootstrap', (event) => {
    const bootstrapEvent = event as MessageEvent<string>;
    const messages = JSON.parse(bootstrapEvent.data) as ViewerMessage[];
    dispatch({ type: 'viewerReset' });
    // Re-inject sprites + default layout after reset so layoutReady becomes true
    // before the agent messages arrive.
    dispatchMockMessages();
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