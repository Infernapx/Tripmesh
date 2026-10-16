import React, { useState, useEffect, useRef } from 'react';

export default function TravelProfile() {
  const [isOpen, setIsOpen] = useState(false);
  const [sessionId, setSessionId] = useState('');
  const [userId, setUserId] = useState('');
  const [copied, setCopied] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (typeof window !== 'undefined' && window.localStorage) {
      setSessionId(window.localStorage.getItem('tripmesh_session_id') || 'sess-active');
      setUserId(window.localStorage.getItem('tripmesh_user_id') || 'usr-active');
    }
  }, [isOpen]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  const handleCopy = () => {
    if (sessionId && navigator.clipboard) {
      navigator.clipboard.writeText(sessionId);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleReset = () => {
    const newId = 'sess-' + Math.random().toString(36).substring(2, 10);
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem('tripmesh_session_id', newId);
    }
    window.location.reload();
  };

  return (
    <div className="relative" ref={popoverRef}>
      {/* 42px Circular Travel-Themed Profile Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="relative group w-11 h-11 rounded-full p-[2px] cursor-pointer focus:outline-none transition-all duration-300 ease-out hover:scale-108 hover:rotate-3"
        style={{
          background: 'linear-gradient(135deg, rgba(36,217,255,0.8) 0%, rgba(131,103,255,0.8) 60%, rgba(212,92,255,0.6) 100%)',
          boxShadow: '0 0 16px rgba(36,217,255,0.35)',
        }}
        aria-label="TripMesh Travel Profile & Session"
        title="Travel Profile & Session"
      >
        <div className="w-full h-full rounded-full bg-[#071D49] flex items-center justify-center transition-all group-hover:bg-[#0A2761]">
          <span className="font-sans font-black text-sm tracking-tight text-[#28DFFF] drop-shadow-[0_0_8px_rgba(40,223,255,0.7)] select-none">
            TM
          </span>
        </div>

        {/* Ambient Halo Pulse on hover */}
        <span
          className="absolute -inset-1 rounded-full opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none"
          style={{
            boxShadow: '0 0 22px rgba(36,217,255,0.7), inset 0 0 10px rgba(131,103,255,0.5)',
          }}
        />
      </button>

      {/* Profile & Session Popover */}
      {isOpen && (
        <div className="absolute right-0 mt-3 w-72 rounded-2xl glass-panel-warm border border-white/[0.12] p-4 text-left shadow-2xl z-50 animate-in fade-in slide-in-from-top-2 duration-200">
          <div className="flex items-center gap-3 pb-3 border-b border-white/[0.08]">
            <div className="w-10 h-10 rounded-full overflow-hidden border border-[#24D9FF]/40">
              <img src="/assets/travel_avatar.png" alt="Profile" className="w-full h-full object-cover" />
            </div>
            <div>
              <div className="text-sm font-bold text-white">Travel Concierge</div>
              <div className="text-[11px] font-mono text-[#24D9FF] flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[#24D9FF] animate-pulse" />
                Session Connected
              </div>
            </div>
          </div>

          <div className="py-3 space-y-2 text-xs">
            <div className="flex justify-between items-center text-slate-300">
              <span className="text-[11px] font-mono text-slate-400">Session ID</span>
              <button
                onClick={handleCopy}
                className="text-[10px] font-mono text-[#24D9FF] hover:underline"
              >
                {copied ? 'Copied!' : 'Copy ID'}
              </button>
            </div>
            <div className="p-2 rounded-lg bg-black/30 font-mono text-[11px] text-slate-300 truncate border border-white/5">
              {sessionId}
            </div>

            <div className="flex justify-between items-center text-slate-300 pt-1">
              <span className="text-[11px] font-mono text-slate-400">User ID</span>
              <span className="font-mono text-[11px] text-slate-300">{userId.slice(0, 10)}...</span>
            </div>
          </div>

          <div className="pt-2 border-t border-white/[0.08]">
            <button
              onClick={handleReset}
              className="w-full py-2 px-3 rounded-xl bg-white/[0.06] hover:bg-[#24D9FF]/15 border border-white/[0.1] hover:border-[#24D9FF]/40 text-xs font-medium text-slate-200 hover:text-white transition-all text-center flex items-center justify-center gap-2 cursor-pointer"
            >
              <span>↺</span>
              <span>Start Fresh Journey</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
