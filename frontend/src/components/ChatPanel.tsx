import React, { useState, useRef, useEffect } from 'react';
import ItineraryPlanCard, { type TripPlanPayload } from './ItineraryPlanCard';
import ChatHistory, { saveArchivedChat, type ArchivedChat } from './ChatHistory';

interface Message {
  id: string;
  sender: 'user' | 'agent' | 'divider';
  text: string;
  timestamp: string;
  plan?: TripPlanPayload;
}


const THINKING_STAGES = [
  'Analyzing journey preferences...',
  'Mapping optimal flight & rail corridors...',
  'Synchronizing boutique stays & accommodations...',
  'Balancing budget ceiling against real-time rates...',
  'Synthesizing personalized AI itinerary...',
];

const HERO_SUGGESTION_CHIPS = [
  { icon: '📍', label: 'A weekend in Tokyo', prompt: 'Plan a weekend trip to Tokyo with great food, tech and sights' },
  { icon: '🌴', label: 'Beach holiday under $800', prompt: 'Find me a beach holiday under $800 for 5 days' },
  { icon: '👨‍👩‍👧', label: 'Family trip to Europe', prompt: 'Plan a family trip to Europe for 7 days with boutique stays and fun activities' },
];

const IN_CHAT_QUICK_TAGS = [
  'Find cheaper hotel',
  'Different flight option',
  'Tokyo to Osaka on 20 Dec',
  'Show prices in INR',
  'Show prices in AUD',
  'Confirm itinerary',
];

const renderMessageContent = (
  text: string,
  plan?: TripPlanPayload,
  onAction?: (actionText: string, structuredPayload?: any) => void
) => {
  if (plan) {
    const lines = text.split('\n');
    const leadLines: string[] = [];
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('####') || trimmed.startsWith('---')) break;
      const cleaned = trimmed.replace(/^###\s*/, '').replace(/[*_]/g, '').trim();
      if (cleaned && cleaned.toLowerCase() !== 'trip plan') {
        leadLines.push(cleaned);
      }
    }
    const leadText = leadLines.join(' ');

    return (
      <div className="space-y-4">
        {leadText && (
          <div className="text-xs text-[#28DFFF] font-mono leading-relaxed bg-[#28DFFF]/10 p-3 rounded-xl border border-[#28DFFF]/25 shadow-xs">
            {leadText}
          </div>
        )}
        <ItineraryPlanCard plan={plan} onAction={onAction} />
      </div>
    );
  }

  const hasPdfExport = text.includes('export.pdf');
  const hasIcsExport = text.includes('export.ics');

  if (hasPdfExport || hasIcsExport) {
    const pdfMatch = text.match(/\(http:\/\/[^\)]+export\.pdf\)/i);
    const icsMatch = text.match(/\(http:\/\/[^\)]+export\.ics\)/i);

    const pdfUrl = pdfMatch ? pdfMatch[0].slice(1, -1) : null;
    const icsUrl = icsMatch ? icsMatch[0].slice(1, -1) : null;

    return (
      <div className="space-y-4">
        <div className="whitespace-pre-wrap leading-relaxed text-sm text-[#FFFFFF]">
          {text.replace(/- 📄 \[.*?\]\(.*?\)/g, '').replace(/- 📅 \[.*?\]\(.*?\)/g, '').trim()}
        </div>

        <div className="pt-3 border-t border-white/[0.1] flex flex-wrap gap-3">
          {pdfUrl && (
            <a
              href={pdfUrl}
              target="_blank"
              rel="noopener noreferrer"
              download
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-gradient-to-r from-[#28DFFF] to-[#087BD8] text-[#071D49] font-bold text-xs shadow-[0_0_16px_rgba(40,223,255,0.45)] hover:opacity-95 transition-all no-underline"
            >
              <span>📄</span>
              <span>Download PDF Itinerary</span>
            </a>
          )}
          {icsUrl && (
            <a
              href={icsUrl}
              target="_blank"
              rel="noopener noreferrer"
              download
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-white/[0.08] hover:bg-white/[0.15] border border-white/[0.18] text-[#FFFFFF] font-mono text-xs transition-all no-underline"
            >
              <span>📅</span>
              <span>Sync with Calendar (.ics)</span>
            </a>
          )}
        </div>
      </div>
    );
  }

  const isEstimated = text.includes('[Estimated]') || text.includes('Estimated');
  return (
    <div className="space-y-2">
      <div className="whitespace-pre-wrap leading-relaxed text-sm text-[#FFFFFF] font-sans">
        {text}
      </div>
      {isEstimated && (
        <div className="mt-2 flex">
          <span className="font-mono text-[9px] uppercase px-2 py-0.5 bg-[#FF7188]/15 border border-[#FF7188]/35 text-[#FF7188] rounded">
            Estimated Reference Rate
          </span>
        </div>
      )}
    </div>
  );
};

export default function ChatPanel() {
  const [sessionId, setSessionId] = useState(() => {
    if (typeof window !== 'undefined') {
      let id = (window.localStorage && window.localStorage.getItem('tripmesh_session_id')) ||
               (window.sessionStorage && window.sessionStorage.getItem('tripmesh_session_id'));
      if (!id) {
        id = 'sess-' + Math.random().toString(36).substring(2, 10);
      }
      if (window.localStorage) window.localStorage.setItem('tripmesh_session_id', id);
      if (window.sessionStorage) window.sessionStorage.setItem('tripmesh_session_id', id);
      return id;
    }
    return 'local-session';
  });

  const [userId] = useState(() => {
    if (typeof window !== 'undefined' && window.localStorage) {
      let uid = window.localStorage.getItem('tripmesh_user_id');
      if (!uid) {
        uid = 'usr-' + Math.random().toString(36).substring(2, 10);
        window.localStorage.setItem('tripmesh_user_id', uid);
      }
      return uid;
    }
    return 'local-user';
  });

  const [messages, setMessages] = useState<Message[]>(() => {
    if (typeof window !== 'undefined' && window.localStorage) {
      const activeId = window.localStorage.getItem('tripmesh_session_id');
      if (activeId) {
        try {
          const saved = window.localStorage.getItem(`tripmesh_msgs_${activeId}`);
          if (saved) {
            return JSON.parse(saved);
          }
        } catch (e) {
          console.error('Failed to restore chat messages:', e);
        }
      }
    }
    return [];
  });

  const [input, setInput] = useState('');
  const [isInputFocused, setIsInputFocused] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [thinkingIndex, setThinkingIndex] = useState(0);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const archiveCurrentSession = () => {
    try {
      const nonDivider = messages.filter((m) => m.sender === 'user' || m.sender === 'agent');
      if (nonDivider.length === 0) return;

      const latestPlanMsg = [...messages].reverse().find((m) => !!m.plan);
      const plan = latestPlanMsg?.plan;

      let routeTitle = 'Custom Journey';
      if (plan && (plan.origin || plan.destination)) {
        const orig = plan.origin_name || plan.origin || 'Origin';
        const dest = plan.destination_name || plan.destination || 'Destination';
        routeTitle = `${orig} → ${dest}`;
      } else {
        const firstUser = messages.find((m) => m.sender === 'user');
        if (firstUser) {
          routeTitle = firstUser.text.slice(0, 32) + (firstUser.text.length > 32 ? '...' : '');
        }
      }

      let dates: string | undefined = undefined;
      if (plan && plan.departure_date) {
        dates = `${plan.departure_date} to ${plan.check_out || plan.departure_date}`;
      }

      let totalCost: string | undefined = undefined;
      if (plan && plan.budget_reconciliation?.total_cost) {
        const curr = plan.display_currency || 'USD';
        totalCost = `${plan.budget_reconciliation.total_cost.toFixed(0)} ${curr}`;
      }

      const archived: ArchivedChat = {
        id: 'chat-' + Math.random().toString(36).substring(2, 9),
        sessionId,
        timestamp: Date.now(),
        routeTitle,
        origin: plan?.origin,
        destination: plan?.destination,
        dates,
        totalCost,
        currency: plan?.display_currency || 'USD',
        messages: [...messages],
        finalPlan: plan,
      };

      saveArchivedChat(userId, archived);
    } catch (err) {
      console.error('Auto-archive failed:', err);
    }
  };

  useEffect(() => {
    // Auto-archive on tab close/refresh if the session has at least one message or plan
    const handleBeforeUnload = () => {
      archiveCurrentSession();
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [messages, sessionId, userId]);

  useEffect(() => {
    if (typeof window !== 'undefined' && window.localStorage && sessionId) {
      try {
        window.localStorage.setItem(`tripmesh_msgs_${sessionId}`, JSON.stringify(messages));
      } catch (e) {
        console.error('Failed to save chat messages:', e);
      }
    }
  }, [messages, sessionId]);

  useEffect(() => {
    // Only scroll internally inside the chat conversation container. NEVER call window.scrollTo or scrollIntoView.
    const container = chatScrollRef.current;
    if (container) {
      container.scrollTo({
        top: container.scrollHeight,
        behavior: 'smooth',
      });
    }
  }, [messages, isLoading]);

  useEffect(() => {
    if (!isLoading) return;
    const timer = setInterval(() => {
      setThinkingIndex((prev) => (prev + 1) % THINKING_STAGES.length);
    }, 2200);
    return () => clearInterval(timer);
  }, [isLoading]);

  useEffect(() => {
    const handleSetPrompt = (e: CustomEvent<{ prompt: string; autoSend?: boolean }>) => {
      if (e.detail?.prompt) {
        setInput(e.detail.prompt);
        if (e.detail.autoSend) {
          sendMessage(e.detail.prompt);
        } else {
          inputRef.current?.focus({ preventScroll: true });
        }
      }
    };

    window.addEventListener('tripmesh:set-prompt' as any, handleSetPrompt);
    return () => window.removeEventListener('tripmesh:set-prompt' as any, handleSetPrompt);
  }, [sessionId, userId, isLoading]);

  const handleResumeTrip = (archived: ArchivedChat) => {
    const newId = 'sess-' + Math.random().toString(36).substring(2, 10);
    setSessionId(newId);
    if (typeof window !== 'undefined') {
      window.localStorage?.setItem('tripmesh_session_id', newId);
    }
    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const resumeDivider: Message = {
      id: 'div-resumed-' + Math.random().toString(36).substring(7),
      sender: 'divider',
      text: `— Resumed journey: ${archived.routeTitle} — ${timeStr}`,
      timestamp: timeStr,
    };
    setMessages([...archived.messages, resumeDivider]);
  };

  const startNewJourney = () => {
    // Archive current session before starting a new one
    archiveCurrentSession();

    const newId = 'sess-' + Math.random().toString(36).substring(2, 10);
    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (typeof window !== 'undefined') {
      if (window.localStorage) {
        window.localStorage.setItem('tripmesh_session_id', newId);
        // User preferences / memory must NOT be cleared: tripmesh_user_id remains intact
      }
      if (window.sessionStorage) {
        window.sessionStorage.setItem('tripmesh_session_id', newId);
      }
    }
    setSessionId(newId);

    const dividerMsg: Message = {
      id: 'div-' + Math.random().toString(36).substring(7),
      sender: 'divider',
      text: `— New Journey started — ${timeStr}`,
      timestamp: timeStr,
    };

    const greetingMsg: Message = {
      id: 'greet-' + Math.random().toString(36).substring(7),
      sender: 'agent',
      text: "No trip yet — tell me where you're headed.",
      timestamp: timeStr,
    };

    setMessages((prev) => [...prev, dividerMsg, greetingMsg]);
    setInput('');
  };


  const sendMessage = async (textToSend: string, structuredPayload?: any) => {
    if (!textToSend.trim() || isLoading) return;

    const currentScrollY = typeof window !== 'undefined' ? window.scrollY : 0;
    console.log("before submit", currentScrollY);

    const userMsg: Message = {
      id: Math.random().toString(36).substring(7),
      sender: 'user',
      text: textToSend,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);
    setThinkingIndex(0);

    setTimeout(() => {
      inputRef.current?.focus({ preventScroll: true });
      if (typeof window !== 'undefined') {
        console.log("after user message render", window.scrollY);
        if (window.scrollY !== currentScrollY) {
          window.scrollTo({ top: currentScrollY, behavior: 'instant' });
        }
      }
    }, 0);

    try {
      const apiBase = (import.meta.env.PUBLIC_API_URL || 'http://localhost:8000').replace(/\/$/, '');
      const response = await fetch(`${apiBase}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: textToSend,
          session_id: sessionId,
          user_id: userId,
          structured_payload: structuredPayload || undefined,
        }),
      });

      if (!response.ok) {
        throw new Error('Gateway response error');
      }

      const data = await response.json();
      const agentMsg: Message = {
        id: Math.random().toString(36).substring(7),
        sender: 'agent',
        text: data.response || 'No response received.',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        plan: data.plan || undefined,
      };
      setMessages((prev) => [...prev, agentMsg]);
    } catch (err) {
      console.error(err);
      const errorMsg: Message = {
        id: Math.random().toString(36).substring(7),
        sender: 'agent',
        text: 'Error connecting to the TripMesh Gateway. Please ensure all mesh services are operational.',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
      setTimeout(() => {
        inputRef.current?.focus({ preventScroll: true });
        if (typeof window !== 'undefined') {
          console.log("after AI message render", window.scrollY);
          if (window.scrollY !== currentScrollY) {
            window.scrollTo({ top: currentScrollY, behavior: 'instant' });
          }
        }
      }, 0);
    }
  };

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;
    const text = input.trim();
    setInput('');
    inputRef.current?.focus({ preventScroll: true });
    await sendMessage(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend(e);
    }
  };

  const hasMessages = messages.length > 0;

  return (
    <div id="concierge" className="w-full flex flex-col items-center">
      {/* 
        HERO CONTAINER: Strict Single Center Axis
        Eyebrow -> Headline -> Subtitle -> Chat Input -> Prompt Chips
        Width: 650-760px desktop
      */}
      <div className="w-full max-w-[740px] flex flex-col items-center text-center">
        {/* EYEBROW & HISTORY BAR */}
        <div className="w-full flex items-center justify-between mb-4 px-2">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-white/[0.08] border border-[#28DFFF]/35 backdrop-blur-md shadow-[0_0_16px_rgba(40,223,255,0.2)]">
            <span className="text-[#28DFFF] text-xs">✦</span>
            <span className="font-mono text-[11px] tracking-widest text-[#28DFFF] uppercase font-semibold">
              YOUR PERSONAL TRAVEL INTELLIGENCE
            </span>
          </div>
          <button
            id="chat-history-pill-button"
            onClick={() => setIsHistoryOpen(true)}
            className="text-xs font-mono px-3 py-1.5 rounded-xl bg-white/[0.06] hover:bg-white/[0.12] text-[#EDEAE1] border border-[#2C5F6B]/40 shadow-xs transition-all cursor-pointer flex items-center gap-1.5"
            title="View past journeys"
          >
            <svg className="w-3.5 h-3.5 text-[#C1662F]" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            <span className="font-medium">History</span>
          </button>
        </div>

        {/* HEADLINE: 'Where will you' in white, 'go next?' in vivid gradient */}
        {!hasMessages ? (
          <>
            <h1
              className="font-sans tracking-tight text-white mb-4 text-center drop-shadow-[0_2px_12px_rgba(0,0,0,0.5)] select-none"
              style={{
                fontSize: 'clamp(58px, 4.7vw, 80px)',
                lineHeight: 0.95,
                fontWeight: 780,
              }}
            >
              Where will you <br />
              <span
                className="bg-clip-text text-transparent inline-block pb-1"
                style={{
                  backgroundImage: 'linear-gradient(90deg, #2ADFFF 0%, #4AA8FF 30%, #8067FF 65%, #E45DD4 100%)',
                }}
              >
                go next?
              </span>
            </h1>

            {/* SUBTITLE: Max 580px, directly below headline */}
            <p className="text-sm sm:text-base text-slate-100 max-w-[580px] mx-auto mb-6 font-normal leading-relaxed text-balance drop-shadow-[0_1px_6px_rgba(0,0,0,0.6)]">
              Chat with TripMesh and get personalized travel plans, flights, stays and experiences — all in one place.
            </p>
          </>
        ) : (
          <div className="flex items-center justify-between w-full px-4 py-2.5 mb-5 rounded-2xl glass-panel-warm border border-white/[0.14]">
            <div className="flex items-center gap-3">
              <span className="w-2.5 h-2.5 rounded-full bg-[#28DFFF] shadow-[0_0_8px_#28DFFF]" />
              <span className="text-sm font-bold text-white tracking-tight">Active Journey Planning</span>
              <span className="font-mono text-[10px] uppercase px-2 py-0.5 rounded-full bg-[#28DFFF]/15 text-[#28DFFF] border border-[#28DFFF]/30">
                Session: {sessionId.slice(0, 10)}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                id="chat-history-button"
                onClick={() => setIsHistoryOpen(true)}
                className="text-xs font-mono px-3 py-1.5 rounded-xl bg-white/[0.06] hover:bg-white/[0.12] text-[#EDEAE1] border border-[#2C5F6B]/40 shadow-xs transition-all cursor-pointer flex items-center gap-1.5"
                title="View past journeys"
              >
                <svg className="w-3.5 h-3.5 text-[#C1662F]" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
                <span className="font-medium">History</span>
              </button>
              <button
                id="new-journey-button"
                onClick={startNewJourney}
                className="text-xs font-mono px-3 py-1.5 rounded-xl bg-gradient-to-r from-[#28DFFF]/20 to-[#8067FF]/20 hover:from-[#28DFFF]/35 hover:to-[#8067FF]/35 text-white border border-[#28DFFF]/40 shadow-sm transition-all cursor-pointer flex items-center gap-1.5"
                title="Start a fresh journey conversation"
              >
                <span>✨</span>
                <span className="font-semibold">New Journey</span>
              </button>
            </div>
          </div>
        )}


        {/* 
          CONVERSATION STREAM (When user has messages)
          Controlled constant height: internal scroll ONLY, does NOT push input or hero!
        */}
        {hasMessages && (
          <div
            ref={chatScrollRef}
            className="chat-messages w-full mb-6 rounded-3xl glass-panel-warm border border-white/[0.14] p-5 sm:p-7 shadow-2xl space-y-6 h-[460px] max-h-[460px] overflow-y-auto text-left"
            style={{
              overflowAnchor: 'none',
              overscrollBehavior: 'contain',
            }}
          >
            {messages.map((msg) => {
              if (msg.sender === 'divider') {
                return (
                  <div key={msg.id} className="relative py-4 my-2 flex items-center justify-center w-full">
                    <div className="absolute inset-0 flex items-center">
                      <div className="w-full border-t border-[#28DFFF]/30" />
                    </div>
                    <div className="relative px-4 py-1 rounded-full bg-[#071D49]/90 border border-[#28DFFF]/40 text-[11px] font-mono text-[#28DFFF] shadow-[0_0_12px_rgba(40,223,255,0.25)] flex items-center gap-1.5">
                      <span>✨</span>
                      <span>{msg.text}</span>
                    </div>
                  </div>
                );
              }

              return (
                <div
                  key={msg.id}
                  className={`flex flex-col ${msg.sender === 'user' ? 'items-end' : 'items-start'} space-y-1.5`}
                >
                  <div className="flex items-center gap-2 px-1 text-[11px] font-mono text-slate-300">
                    {msg.sender === 'agent' && (
                      <span className="w-4 h-4 rounded-full bg-gradient-to-tr from-[#28DFFF] to-[#8067FF] flex items-center justify-center text-[9px] text-white">
                        ✦
                      </span>
                    )}
                    <span>{msg.sender === 'user' ? 'You' : 'TripMesh AI Concierge'}</span>
                    <span>•</span>
                    <span>{msg.timestamp}</span>
                  </div>

                  <div
                    className={`max-w-[94%] sm:max-w-[88%] rounded-2xl p-4 sm:p-5 text-sm transition-all ${
                      msg.sender === 'user'
                        ? 'bg-[#0E2F60]/90 border border-[#28DFFF]/40 text-white shadow-md'
                        : 'bg-[#091D3E]/90 border border-white/[0.16] text-[#FFFFFF] shadow-[0_8px_30px_rgba(2,10,28,0.7)]'
                    }`}
                  >
                    {renderMessageContent(msg.text, msg.plan, (actionText, payload) => sendMessage(actionText, payload))}
                  </div>
                </div>
              );
            })}

            {/* AI Thinking Animation */}
            {isLoading && (
              <div className="flex items-start gap-3 py-3 px-2">
                <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-[#28DFFF] to-[#8067FF] flex items-center justify-center text-white text-xs shadow-[0_0_14px_rgba(40,223,255,0.45)]">
                  ✦
                </div>
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-white">TripMesh Concierge</span>
                    <div className="flex gap-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-[#28DFFF] animate-pulse" />
                      <span className="w-1.5 h-1.5 rounded-full bg-[#7667FF] animate-pulse" style={{ animationDelay: '150ms' }} />
                      <span className="w-1.5 h-1.5 rounded-full bg-[#EC5BC8] animate-pulse" style={{ animationDelay: '300ms' }} />
                    </div>
                  </div>
                  <div className="font-mono text-xs text-[#28DFFF]">
                    {THINKING_STAGES[thinkingIndex]}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* 
          CHAT INPUT: PRIMARY PRODUCT CTA
          Sits DIRECTLY below subtitle sharing the EXACT same center axis.
          Height: 72px, Radius: 32px, Width: max-w-[720px] mx-auto.
        */}
        <div className="w-full max-w-[720px] mx-auto">
          <div className={`concierge-glass-input-wrapper ${isInputFocused ? 'is-focused' : ''}`}>
            <form
              onSubmit={handleSend}
              className="concierge-glass-input flex items-center px-5 sm:px-6 w-full group"
            >
              {/* Left: Sparkle / AI Symbol (24px, rotates slightly on hover) */}
              <div className="flex items-center justify-center pr-3 text-[#28DFFF] transition-transform duration-300 group-hover:rotate-12 select-none shrink-0">
                <svg
                  className="w-6 h-6 drop-shadow-[0_0_10px_rgba(40,223,255,0.7)]"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                >
                  <path d="M12 2L14.4 8.6L21 11L14.4 13.4L12 20L9.6 13.4L3 11L9.6 8.6L12 2Z" />
                </svg>
              </div>

              {/* Main Input Field */}
              <input
                ref={inputRef}
                id="chat-input"
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                onFocus={() => setIsInputFocused(true)}
                onBlur={() => setIsInputFocused(false)}
                placeholder="Tell me where you want to go..."
                className="w-full bg-transparent text-sm sm:text-base text-white placeholder-slate-300 focus:outline-none py-2 px-1 font-normal"
                autoComplete="off"
              />

              {/* Right: Circular Arrow / Send Button (cyan -> blue -> violet -> magenta) */}
              <button
                type="submit"
                disabled={isLoading || !input.trim()}
                aria-label="Send message"
                className={`w-12 h-12 rounded-full flex items-center justify-center transition-all duration-300 shrink-0 cursor-pointer shadow-md ${
                  input.trim() && !isLoading
                    ? 'text-white hover:scale-105 active:scale-95 shadow-[0_0_20px_rgba(118,103,255,0.7)]'
                    : 'bg-white/[0.08] text-white/40 border border-white/[0.1] cursor-not-allowed'
                }`}
                style={
                  input.trim() && !isLoading
                    ? {
                        background: 'linear-gradient(135deg, #28DFFF 0%, #087BD8 35%, #7667FF 70%, #EC5BC8 100%)',
                      }
                    : undefined
                }
              >
                <svg className="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M14 5l7 7m0 0l-7 7m7-7H3" />
                </svg>
              </button>
            </form>
          </div>

          {/* 
            PROMPT CHIPS: Exactly 3 chips forming ONE centered row directly below input
            Hover: border color change, icon bounce, NO chip translation
          */}
          {!hasMessages ? (
            <div className="flex flex-wrap items-center justify-center gap-3 mt-4 w-full">
              {HERO_SUGGESTION_CHIPS.map((chip, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => sendMessage(chip.prompt)}
                  className="suggestion-chip px-4 py-2 rounded-full text-xs font-medium text-slate-100 hover:text-white flex items-center gap-2 cursor-pointer shadow-xs group"
                >
                  <span className="transition-transform duration-200 group-hover:scale-125 inline-block select-none">{chip.icon}</span>
                  <span className="group-hover:text-white transition-colors">{chip.label}</span>
                </button>
              ))}
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-center gap-2 mt-3 pt-1">
              <span className="text-[11px] font-mono text-slate-300">Quick refinements:</span>
              {IN_CHAT_QUICK_TAGS.map((tag, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => sendMessage(tag)}
                  className="px-3 py-1 rounded-full text-[11px] font-mono bg-white/[0.06] hover:bg-[#28DFFF]/20 border border-white/[0.12] hover:border-[#28DFFF]/40 text-slate-200 hover:text-[#28DFFF] transition-all cursor-pointer"
                >
                  {tag}
                </button>
              ))}
              {messages.some((m) => !!m.plan) && (
                <button
                  type="button"
                  onClick={startNewJourney}
                  className="px-3 py-1 rounded-full text-[11px] font-mono bg-[#28DFFF]/20 hover:bg-[#28DFFF]/30 border border-[#28DFFF]/50 text-[#28DFFF] font-semibold transition-all cursor-pointer flex items-center gap-1 shadow-[0_0_10px_rgba(40,223,255,0.2)]"
                  title="Start a fresh journey"
                >
                  <span>✨</span>
                  <span>New Journey</span>
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Slide-over Journey History Drawer */}
      <ChatHistory
        isOpen={isHistoryOpen}
        onClose={() => setIsHistoryOpen(false)}
        userId={userId}
        onResume={handleResumeTrip}
      />
    </div>
  );
}

