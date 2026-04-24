import { useCallback, useEffect, useState } from 'react';

import { Button } from './ui/Button.js';
import { Modal } from './ui/Modal.js';
import {
  listReplaySessions,
  loadReplay,
  type ReplaySessionRef,
} from '../remoteViewer.js';

interface ReplayPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export function ReplayPanel({ isOpen, onClose }: ReplayPanelProps) {
  const [sessions, setSessions] = useState<ReplaySessionRef[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeSid, setActiveSid] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listReplaySessions();
      setSessions(list);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) void refresh();
  }, [isOpen, refresh]);

  const handleLoad = useCallback(async (ref: ReplaySessionRef) => {
    setActiveSid(ref.sessionId);
    setError(null);
    try {
      await loadReplay(ref);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setActiveSid(null);
    }
  }, [onClose]);

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Replay sessions" className="max-w-2xl w-[640px]">
      <div className="flex items-center justify-between mb-3 px-2">
        <span className="text-sm text-fg-muted">
          {loading ? 'loading...' : `${sessions.length} session(s)`}
        </span>
        <Button variant="ghost" size="sm" onClick={refresh} disabled={loading}>
          refresh
        </Button>
      </div>
      {error && (
        <div className="text-sm text-red-400 mb-3 px-2 break-all">{error}</div>
      )}
      <div className="max-h-96 overflow-y-auto border border-border">
        {sessions.length === 0 && !loading && (
          <div className="p-4 text-sm text-fg-muted">No recorded sessions.</div>
        )}
        {sessions.map((s) => {
          const key = `${s.providerId}/${s.sessionId}`;
          const busy = activeSid === s.sessionId;
          return (
            <div
              key={key}
              className="flex items-center justify-between gap-2 px-3 py-2 border-b border-border last:border-b-0"
            >
              <div className="flex flex-col min-w-0">
                <span className="text-xs text-accent-bright">{s.providerId}</span>
                <span className="text-xs font-mono truncate" title={s.sessionId}>
                  {s.sessionId}
                </span>
              </div>
              <Button
                variant="accent"
                size="sm"
                onClick={() => void handleLoad(s)}
                disabled={busy}
              >
                {busy ? '...' : 'load'}
              </Button>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}
