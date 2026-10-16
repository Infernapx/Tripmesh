import React, { useState, useEffect } from 'react';
import type { TripPlanPayload } from './ItineraryPlanCard';
import ItineraryPlanCard from './ItineraryPlanCard';

export interface ArchivedChat {
  id: string;
  sessionId: string;
  timestamp: number;
  routeTitle: string;
  origin?: string;
  destination?: string;
  dates?: string;
  totalCost?: string;
  currency?: string;
  messages: Array<{
    id: string;
    sender: 'user' | 'agent' | 'divider';
    text: string;
    timestamp: string;
    plan?: TripPlanPayload;
  }>;
  finalPlan?: TripPlanPayload;
}

const STORAGE_PREFIX = 'tripmesh:chats:';
const MAX_CHATS = 30;

export function getStorageKey(userId: string): string {
  return `${STORAGE_PREFIX}${userId || 'guest'}`;
}

export function loadArchivedChats(userId: string): ArchivedChat[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = localStorage.getItem(getStorageKey(userId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return parsed.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    }
  } catch (err) {
    console.error('Failed to load archived chats from localStorage:', err);
  }
  return [];
}

export function saveArchivedChat(userId: string, chat: ArchivedChat): void {
  if (typeof window === 'undefined') return;
  try {
    const existing = loadArchivedChats(userId);
    // Remove if already exists with same sessionId or id
    const filtered = existing.filter((c) => c.id !== chat.id && c.sessionId !== chat.sessionId);
    // Add newest at top
    const updated = [chat, ...filtered].slice(0, MAX_CHATS);
    localStorage.setItem(getStorageKey(userId), JSON.stringify(updated));
  } catch (err) {
    console.error('Failed to save archived chat to localStorage:', err);
  }
}

export function deleteArchivedChat(userId: string, chatId: string): ArchivedChat[] {
  if (typeof window === 'undefined') return [];
  try {
    const existing = loadArchivedChats(userId);
    const updated = existing.filter((c) => c.id !== chatId);
    localStorage.setItem(getStorageKey(userId), JSON.stringify(updated));
    return updated;
  } catch (err) {
    console.error('Failed to delete archived chat from localStorage:', err);
    return [];
  }
}

function formatRelativeTime(timestamp: number): string {
  const diff = Date.now() - timestamp;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(timestamp).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

interface ChatHistoryProps {
  isOpen: boolean;
  onClose: () => void;
  userId: string;
  onResume: (chat: ArchivedChat) => void;
}

export default function ChatHistory({ isOpen, onClose, userId, onResume }: ChatHistoryProps) {
  const [chats, setChats] = useState<ArchivedChat[]>([]);
  const [selectedChat, setSelectedChat] = useState<ArchivedChat | null>(null);

  useEffect(() => {
    if (isOpen) {
      setChats(loadArchivedChats(userId));
      setSelectedChat(null);
    }
  }, [isOpen, userId]);

  const handleDelete = (e: React.MouseEvent, chatId: string) => {
    e.stopPropagation();
    const updated = deleteArchivedChat(userId, chatId);
    setChats(updated);
    if (selectedChat?.id === chatId) {
      setSelectedChat(null);
    }
  };

  const handleResume = (chat: ArchivedChat) => {
    onResume(chat);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-xs transition-opacity duration-300">
      <div
        className="w-full max-w-[520px] h-full flex flex-col shadow-2xl border-l border-[#2C5F6B]/40 animate-in slide-in-from-right duration-300"
        style={{ backgroundColor: '#12232E' }}
      >
        {/* Header */}
        <div className="px-6 py-5 border-b border-[#2C5F6B]/30 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <svg
              className="w-5 h-5 text-[#C1662F]"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            <h2 className="font-serif text-lg font-medium text-[#EDEAE1] tracking-tight">
              {selectedChat ? 'Journey details' : 'Past journeys'}
            </h2>
          </div>
          <button
            onClick={selectedChat ? () => setSelectedChat(null) : onClose}
            className="p-1.5 rounded-lg text-[#7FA8AE] hover:text-[#EDEAE1] hover:bg-white/5 transition-colors cursor-pointer"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {selectedChat ? (
            /* Selected Chat Detail View */
            <div className="space-y-6">
              <div className="p-4 rounded-xl bg-white/[0.04] border border-[#2C5F6B]/30 space-y-2">
                <div className="flex items-center justify-between">
                  <h3 className="text-base font-serif font-medium text-[#EDEAE1]">
                    {selectedChat.routeTitle}
                  </h3>
                  <span className="font-mono text-xs text-[#7FA8AE]">
                    {formatRelativeTime(selectedChat.timestamp)}
                  </span>
                </div>
                {selectedChat.dates && (
                  <p className="font-mono text-xs text-[#7FA8AE]">
                    📅 {selectedChat.dates}
                  </p>
                )}
                {selectedChat.totalCost && (
                  <p className="font-mono text-sm text-[#B9973F] font-semibold">
                    💰 {selectedChat.totalCost}
                  </p>
                )}
              </div>

              {/* Action: Resume */}
              <button
                onClick={() => handleResume(selectedChat)}
                className="w-full py-2.5 px-4 rounded-xl bg-[#C1662F] hover:bg-[#d47235] text-white font-sans font-medium text-sm transition-all shadow-md flex items-center justify-center gap-2 cursor-pointer"
              >
                <span>🔄</span>
                <span>Resume this trip</span>
              </button>

              {/* Read-only Transcript */}
              <div className="space-y-4 pt-2">
                <h4 className="text-xs font-mono uppercase text-[#7FA8AE] tracking-wider">
                  Conversation transcript
                </h4>
                <div className="space-y-3">
                  {selectedChat.messages.map((m) => (
                    <div
                      key={m.id}
                      className={`p-3.5 rounded-xl text-sm ${
                        m.sender === 'user'
                          ? 'bg-[#2C5F6B]/25 text-[#EDEAE1] ml-6 border border-[#2C5F6B]/30'
                          : m.sender === 'divider'
                          ? 'text-center text-xs text-[#7FA8AE] italic py-1'
                          : 'bg-white/[0.03] text-[#EDEAE1] mr-6 border border-white/5'
                      }`}
                    >
                      <div className="whitespace-pre-wrap leading-relaxed">{m.text}</div>
                      {m.plan && (
                        <div className="mt-3 pt-3 border-t border-white/10">
                          <ItineraryPlanCard plan={m.plan} />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : chats.length === 0 ? (
            /* Empty State */
            <div className="h-full flex flex-col items-center justify-center text-center p-8 space-y-3 my-auto">
              <span className="text-3xl text-[#7FA8AE]">🧭</span>
              <p className="text-base font-serif text-[#EDEAE1]">No past journeys yet</p>
              <p className="text-xs text-[#7FA8AE] max-w-[260px] leading-relaxed">
                Tell TripMesh where you'd like to travel. Completed journeys will automatically save here.
              </p>
            </div>
          ) : (
            /* Journey List */
            <div className="space-y-3">
              {chats.map((chat) => (
                <div
                  key={chat.id}
                  onClick={() => setSelectedChat(chat)}
                  className="group relative p-4 rounded-xl bg-white/[0.03] hover:bg-white/[0.07] border border-[#2C5F6B]/30 hover:border-[#7FA8AE]/50 transition-all cursor-pointer flex items-center justify-between space-x-3"
                >
                  <div className="space-y-1 min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-serif font-medium text-[#EDEAE1] truncate">
                        {chat.routeTitle}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 font-mono text-[11px] text-[#7FA8AE]">
                      {chat.dates && <span>{chat.dates}</span>}
                      {chat.totalCost && (
                        <span className="text-[#B9973F] font-semibold">{chat.totalCost}</span>
                      )}
                      <span>• {formatRelativeTime(chat.timestamp)}</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      onClick={(e) => handleDelete(e, chat.id)}
                      className="p-1.5 rounded-lg text-[#7FA8AE] hover:text-[#B23A32] hover:bg-white/5 opacity-80 group-hover:opacity-100 transition-all cursor-pointer"
                      title="Delete journey"
                      aria-label="Delete journey"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      </svg>
                    </button>
                    <span className="text-[#7FA8AE] text-sm group-hover:translate-x-0.5 transition-transform">
                      →
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-[#2C5F6B]/30 flex items-center justify-between text-[11px] font-mono text-[#7FA8AE]">
          <span>Saved journeys: {chats.length} / {MAX_CHATS}</span>
          <span>TripMesh Journal</span>
        </div>
      </div>
    </div>
  );
}
