import React, { useState, useEffect, useRef, useCallback } from 'react';

interface TraceItem {
  id: string;
  session_id: string;
  agent: string;
  skill: string;
  status: 'running' | 'success' | 'error' | 'no_change';
  decision_reason?: string | null;
  start_time: string;
  end_time?: string | null;
  duration_ms?: number | null;
  metadata?: Record<string, any>;
}

interface Waypoint {
  id: string;
  code: string;
  name: string;
  agentKey: string;
}

const WAYPOINTS: Waypoint[] = [
  { id: 'planner', code: 'PL', name: 'Planner', agentKey: 'planner_agent' },
  { id: 'transport', code: 'TR', name: 'Transport', agentKey: 'transport_agent' },
  { id: 'hotel', code: 'HT', name: 'Hotel', agentKey: 'hotel_agent' },
  { id: 'guide', code: 'LG', name: 'Local Guide', agentKey: 'local_guide_agent' },
  { id: 'budget', code: 'BG', name: 'Budget', agentKey: 'budget_agent' },
];

export default function AgentTrace() {
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [isPollingActive, setIsPollingActive] = useState(false);
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null);
  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(null);
  const pollingTimerRef = useRef<NodeJS.Timeout | null>(null);

  const getSessionId = useCallback(() => {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      return window.sessionStorage.getItem('tripmesh_session_id') || 'local-session';
    }
    return 'local-session';
  }, []);

  const fetchTraces = useCallback(async () => {
    const sessionId = getSessionId();
    try {
      const apiBase = (import.meta.env.PUBLIC_API_URL || 'http://localhost:8000').replace(/\/$/, '');
      const resp = await fetch(`${apiBase}/api/trip/${sessionId}/traces`);
      if (resp.ok) {
        const data = await resp.json();
        if (Array.isArray(data.traces)) {
          setTraces(data.traces);
          setLastFetchedAt(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
        }
      }
    } catch (err) {
      // Gateway might not be ready or network down; graceful silent fail
      console.debug('Trace polling fetch error:', err);
    }
  }, [getSessionId]);

  // Handle active polling state triggered by chat activity
  useEffect(() => {
    const handleChatStart = () => {
      setIsPollingActive(true);
      fetchTraces();
    };

    const handleChatEnd = () => {
      // Delay deactivation slightly to catch final trace events on turn completion
      setTimeout(() => {
        fetchTraces().then(() => {
          setIsPollingActive(false);
        });
      }, 500);
    };

    window.addEventListener('tripmesh:chat-start', handleChatStart);
    window.addEventListener('tripmesh:chat-end', handleChatEnd);

    // Initial fetch on mount
    fetchTraces();

    return () => {
      window.removeEventListener('tripmesh:chat-start', handleChatStart);
      window.removeEventListener('tripmesh:chat-end', handleChatEnd);
    };
  }, [fetchTraces]);

  // Polling interval: active only during requests to prevent request storm when idle
  useEffect(() => {
    if (isPollingActive) {
      pollingTimerRef.current = setInterval(() => {
        fetchTraces();
      }, 1000);
    } else if (pollingTimerRef.current) {
      clearInterval(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }

    return () => {
      if (pollingTimerRef.current) {
        clearInterval(pollingTimerRef.current);
        pollingTimerRef.current = null;
      }
    };
  }, [isPollingActive, fetchTraces]);

  // Determine waypoint states
  const activeAgentKey = traces.length > 0 && isPollingActive ? traces[traces.length - 1].agent : null;
  const activatedAgentKeys = new Set(traces.map((t) => t.agent));

  return (
    <div className="border border-horizon-300 rounded-lg p-4 bg-paper-50 dark:bg-ink-900 shadow-sm flex flex-col h-[520px]">
      {/* Header with Title and Mode indicator */}
      <div className="flex items-center justify-between pb-3 border-b border-horizon-300/30">
        <div>
          <h3 className="font-display text-lg font-medium text-horizon-600 dark:text-horizon-300 flex items-center gap-2">
            <span>Agent Trace</span>
            {isPollingActive && (
              <span className="inline-flex items-center gap-1 font-mono text-[10px] text-stamp-500 font-normal">
                <span className="w-1.5 h-1.5 rounded-full bg-stamp-500 motion-safe:animate-pulse motion-reduce:animate-none"></span>
                live
              </span>
            )}
          </h3>
          <p className="text-[11px] text-horizon-300 font-sans mt-0.5">
            Cartographic internal reasoning &amp; decision log
          </p>
        </div>
        <div className="flex items-center gap-2">
          {lastFetchedAt && (
            <span className="font-mono text-[10px] text-horizon-300/80 hidden sm:inline">
              synced {lastFetchedAt}
            </span>
          )}
          <button
            onClick={() => fetchTraces()}
            title="Refresh trace"
            className="p-1.5 text-horizon-300 hover:text-horizon-600 dark:hover:text-paper-50 rounded hover:bg-horizon-300/10 transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
        </div>
      </div>

      {/* Signature Waypoint Flight Path Map */}
      <div className="py-4 px-2 border-b border-horizon-300/30">
        <div className="flex items-center justify-between relative">
          {/* Connecting Flight Path (Dashed line) */}
          <div
            className="absolute left-4 right-4 top-1/2 -translate-y-1/2 h-[2px] z-0 pointer-events-none"
            style={{
              backgroundImage: 'repeating-linear-gradient(90deg, #7FA8AE 0, #7FA8AE 4px, transparent 4px, transparent 8px)',
              opacity: 0.45,
            }}
          />

          {WAYPOINTS.map((wp) => {
            const isCompleted = activatedAgentKeys.has(wp.agentKey);
            const isCurrentlyRunning = isPollingActive && activeAgentKey === wp.agentKey;

            return (
              <div key={wp.id} className="flex flex-col items-center relative z-10">
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center font-mono text-[11px] font-medium transition-all duration-300 border-2 ${
                    isCurrentlyRunning
                      ? 'border-stamp-500 bg-stamp-500 text-paper-50 shadow-md ring-4 ring-stamp-500/20 motion-safe:animate-pulse motion-reduce:animate-none'
                      : isCompleted
                      ? 'border-horizon-600 bg-horizon-600 text-paper-50'
                      : 'border-horizon-300/40 bg-paper-50 dark:bg-ink-900 text-horizon-300'
                  }`}
                  title={`${wp.name} Agent (${isCurrentlyRunning ? 'Running' : isCompleted ? 'Invoked' : 'Inactive'})`}
                >
                  {wp.code}
                </div>
                <span
                  className={`text-[10px] font-sans mt-1.5 transition-colors ${
                    isCurrentlyRunning
                      ? 'text-stamp-500 font-semibold'
                      : isCompleted
                      ? 'text-ink-900 dark:text-paper-50 font-medium'
                      : 'text-horizon-300/70'
                  }`}
                >
                  {wp.name}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Chronological Event Feed */}
      <div className="flex-grow overflow-y-auto pt-3 space-y-2.5 pr-1">
        {traces.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center p-4 text-horizon-300">
            <svg className="w-8 h-8 mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
            </svg>
            <p className="text-xs font-mono">No agent calls recorded yet.</p>
            <p className="text-[11px] mt-1 text-horizon-300/70">
              Send a trip request in the chat to watch internal waypoints activate.
            </p>
          </div>
        ) : (
          traces.map((trace, idx) => {
            const isExpanded = expandedTraceId === trace.id;
            const isHonestyNotice = trace.status === 'no_change' || (trace.decision_reason && (trace.decision_reason.includes('no cheaper') || trace.decision_reason.includes('no later')));

            return (
              <div
                key={trace.id || idx}
                className={`border rounded p-2.5 text-xs transition-colors ${
                  trace.status === 'error'
                    ? 'border-signal-red/40 bg-signal-red/5'
                    : isHonestyNotice
                    ? 'border-brass-400/40 bg-brass-400/5'
                    : 'border-horizon-300/40 bg-paper-50/60 dark:bg-ink-900/60 hover:border-horizon-600/40'
                }`}
              >
                {/* Event header line */}
                <div
                  className="flex items-center justify-between cursor-pointer select-none"
                  onClick={() => setExpandedTraceId(isExpanded ? null : trace.id)}
                >
                  <div className="flex items-center gap-2">
                    <span className="px-1.5 py-0.5 rounded font-mono text-[10px] font-semibold bg-horizon-600 text-paper-50 uppercase">
                      {trace.agent.replace('_agent', '').replace('_', ' ')}
                    </span>
                    <span className="font-mono text-[11px] text-ink-900 dark:text-paper-50 font-medium">
                      {trace.skill}
                    </span>
                  </div>

                  <div className="flex items-center gap-2">
                    {trace.duration_ms !== null && trace.duration_ms !== undefined && (
                      <span className="font-mono text-[10px] text-horizon-300">
                        {trace.duration_ms.toFixed(1)}ms
                      </span>
                    )}

                    <span
                      className={`text-[9px] font-mono uppercase px-1.5 py-0.5 rounded border ${
                        trace.status === 'success'
                          ? 'border-horizon-600/30 text-horizon-600 dark:text-horizon-300 bg-horizon-600/10'
                          : trace.status === 'no_change'
                          ? 'border-brass-400/40 text-brass-400 bg-brass-400/10'
                          : trace.status === 'error'
                          ? 'border-signal-red/30 text-signal-red bg-signal-red/10'
                          : 'border-stamp-500/30 text-stamp-500 bg-stamp-500/10'
                      }`}
                    >
                      {trace.status === 'no_change' ? 'honest: verified' : trace.status}
                    </span>

                    <span className="text-horizon-300 text-[10px]">
                      {isExpanded ? '▲' : '▼'}
                    </span>
                  </div>
                </div>

                {/* Always-visible or expanded decision reason */}
                {trace.decision_reason && (
                  <div className="mt-2 pt-2 border-t border-horizon-300/20 text-ink-900 dark:text-paper-50 font-sans text-[11px] leading-relaxed">
                    <div className="flex items-start gap-1.5">
                      <span className="text-stamp-500 select-none">↳</span>
                      <p className="flex-grow">{trace.decision_reason}</p>
                    </div>
                  </div>
                )}

                {/* Expanded metadata drawer */}
                {isExpanded && trace.metadata && Object.keys(trace.metadata).length > 0 && (
                  <div className="mt-2 pt-2 border-t border-horizon-300/10 font-mono text-[10px] text-horizon-300/90 space-y-1">
                    {Object.entries(trace.metadata).map(([key, val]) => (
                      <div key={key} className="flex gap-2">
                        <span className="font-semibold text-horizon-600 dark:text-horizon-300">{key}:</span>
                        <span className="truncate">{typeof val === 'object' ? JSON.stringify(val) : String(val)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Footer / Status Note */}
      <div className="pt-2 border-t border-horizon-300/30 flex justify-between items-center text-[10px] font-mono text-horizon-300">
        <span>{traces.length} spans recorded</span>
        <span>Polling: {isPollingActive ? 'Active (1Hz)' : 'Standby'}</span>
      </div>
    </div>
  );
}
