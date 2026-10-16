import React, { useState, useEffect, useRef } from 'react';

interface DatePickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialDeparture?: string;
  initialReturn?: string;
  onApply: (departure: string, returnDate: string, chatLine: string) => void;
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
];

const SHORT_MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
];

const DAYS_OF_WEEK = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

function formatDateYMD(d: Date): string {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function parseDateYMD(str?: string): Date | null {
  if (!str) return null;
  const parts = str.split('T')[0].split('-');
  if (parts.length !== 3) return null;
  const d = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
  return isNaN(d.getTime()) ? null : d;
}

function formatPrettyDate(str: string): string {
  const d = parseDateYMD(str);
  if (!d) return str;
  return `${d.getDate()} ${SHORT_MONTHS[d.getMonth()]}`;
}

export const DatePickerModal: React.FC<DatePickerModalProps> = ({
  isOpen,
  onClose,
  initialDeparture,
  initialReturn,
  onApply,
}) => {
  const modalRef = useRef<HTMLDivElement>(null);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayYMD = formatDateYMD(today);

  // Initialize selected dates
  const [depDate, setDepDate] = useState<string | null>(initialDeparture || todayYMD);
  const [retDate, setRetDate] = useState<string | null>(initialReturn || null);
  const [hoverDate, setHoverDate] = useState<string | null>(null);

  // Month navigation: default to departure month or today's month
  const initialDateObj = parseDateYMD(initialDeparture) || today;
  const [viewYear, setViewYear] = useState(initialDateObj.getFullYear());
  const [viewMonth, setViewMonth] = useState(initialDateObj.getMonth());

  useEffect(() => {
    if (isOpen) {
      const d = parseDateYMD(initialDeparture) || today;
      setDepDate(initialDeparture || todayYMD);
      setRetDate(initialReturn || null);
      setViewYear(d.getFullYear());
      setViewMonth(d.getMonth());
    }
  }, [isOpen, initialDeparture, initialReturn]);

  // Focus trap and Escape key listener
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      } else if (e.key === 'Tab') {
        if (!modalRef.current) return;
        const focusable = modalRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  // Max 11 months ahead
  const maxDate = new Date(today);
  maxDate.setMonth(maxDate.getMonth() + 11);

  const canGoPrev = viewYear > today.getFullYear() || (viewYear === today.getFullYear() && viewMonth > today.getMonth());
  const canGoNext = viewYear < maxDate.getFullYear() || (viewYear === maxDate.getFullYear() && viewMonth < maxDate.getMonth());

  const handlePrevMonth = () => {
    if (!canGoPrev) return;
    if (viewMonth === 0) {
      setViewMonth(11);
      setViewYear((y) => y - 1);
    } else {
      setViewMonth((m) => m - 1);
    }
  };

  const handleNextMonth = () => {
    if (!canGoNext) return;
    if (viewMonth === 11) {
      setViewMonth(0);
      setViewYear((y) => y + 1);
    } else {
      setViewMonth((m) => m + 1);
    }
  };

  // Range selection click handler
  const handleDayClick = (ymd: string) => {
    if (ymd < todayYMD) return;

    if (!depDate || (depDate && retDate)) {
      // First click: select departure date
      setDepDate(ymd);
      setRetDate(null);
    } else if (depDate && !retDate) {
      // Second click: select return date
      if (ymd < depDate) {
        // If clicked date is before departure, reset departure to clicked
        setDepDate(ymd);
        setRetDate(null);
      } else {
        setRetDate(ymd);
      }
    }
  };

  // Calculate nights
  let nightsCount = 0;
  if (depDate && retDate) {
    const d1 = parseDateYMD(depDate);
    const d2 = parseDateYMD(retDate);
    if (d1 && d2) {
      nightsCount = Math.max(0, Math.round((d2.getTime() - d1.getTime()) / (1000 * 60 * 60 * 24)));
    }
  }

  const handleApply = () => {
    if (!depDate) return;
    const finalReturn = retDate || depDate;
    let chatLine = '';
    if (finalReturn && finalReturn !== depDate) {
      chatLine = `Dates changed to ${formatPrettyDate(depDate)} - ${formatPrettyDate(finalReturn)}`;
    } else {
      chatLine = `Dates changed to ${formatPrettyDate(depDate)}`;
    }
    onApply(depDate, finalReturn, chatLine);
    onClose();
  };

  // Build calendar grid days
  const firstDayOfMonth = new Date(viewYear, viewMonth, 1).getDay();
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();

  const calendarDays: Array<{ ymd: string; dayNum: number; isPast: boolean; isCurrentMonth: boolean }> = [];

  // Blank slots for previous month
  for (let i = 0; i < firstDayOfMonth; i++) {
    calendarDays.push({ ymd: '', dayNum: 0, isPast: true, isCurrentMonth: false });
  }

  // Days of current month
  for (let day = 1; day <= daysInMonth; day++) {
    const d = new Date(viewYear, viewMonth, day);
    const ymd = formatDateYMD(d);
    calendarDays.push({
      ymd,
      dayNum: day,
      isPast: ymd < todayYMD,
      isCurrentMonth: true,
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md animate-fade-in"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="date-picker-title"
    >
      <div
        ref={modalRef}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-2xl border border-white/[0.14] bg-[#0A1830]/95 backdrop-blur-2xl shadow-[0_20px_60px_rgba(2,10,24,0.85)] text-white overflow-hidden p-6 space-y-5 animate-scale-up"
      >
        {/* Top Accent Line */}
        <div className="h-[2px] -mx-6 -mt-6 bg-gradient-to-r from-transparent via-[#24D9FF] to-[#8367FF]" />

        {/* Modal Header */}
        <div className="flex items-center justify-between">
          <div>
            <span className="font-mono text-[10px] uppercase font-bold tracking-widest px-2 py-0.5 rounded-full bg-[#24D9FF]/10 border border-[#24D9FF]/30 text-[#24D9FF]">
              CALENDAR SELECTION
            </span>
            <h3 id="date-picker-title" className="text-xl font-sans font-bold text-white mt-1">
              Select Trip Dates
            </h3>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.08] flex items-center justify-center text-slate-400 hover:text-white transition-all cursor-pointer"
            aria-label="Close calendar"
          >
            ✕
          </button>
        </div>

        {/* Selected Range Display Banner */}
        <div className="p-3.5 rounded-xl bg-white/[0.03] border border-white/[0.08] flex items-center justify-between text-xs font-mono">
          <div className="space-y-0.5">
            <span className="text-[10px] uppercase text-slate-400">Departure</span>
            <div className="text-sm font-bold text-[#24D9FF]">
              {depDate ? formatPrettyDate(depDate) : 'Select date'}
            </div>
          </div>
          <div className="text-slate-500 text-sm">━━✈━━</div>
          <div className="space-y-0.5 text-right">
            <span className="text-[10px] uppercase text-slate-400">Return</span>
            <div className="text-sm font-bold text-[#8367FF]">
              {retDate ? formatPrettyDate(retDate) : (depDate ? 'Same day / Pick return' : '—')}
            </div>
          </div>
          {nightsCount > 0 && (
            <div className="pl-3 border-l border-white/[0.1] text-right">
              <span className="text-[10px] uppercase text-slate-400 block">Duration</span>
              <span className="font-bold text-[#00F5D4] text-xs">
                {nightsCount} {nightsCount === 1 ? 'Night' : 'Nights'}
              </span>
            </div>
          )}
        </div>

        {/* Month Navigation */}
        <div className="flex items-center justify-between pt-1">
          <button
            onClick={handlePrevMonth}
            disabled={!canGoPrev}
            className={`px-3 py-1.5 rounded-lg border border-white/[0.08] font-mono text-xs transition-all ${
              canGoPrev
                ? 'bg-white/[0.04] hover:bg-white/[0.08] text-white cursor-pointer'
                : 'opacity-30 cursor-not-allowed text-slate-600'
            }`}
            aria-label="Previous month"
          >
            ◀ Prev
          </button>
          <span className="font-sans font-bold text-sm text-white">
            {MONTH_NAMES[viewMonth]} {viewYear}
          </span>
          <button
            onClick={handleNextMonth}
            disabled={!canGoNext}
            className={`px-3 py-1.5 rounded-lg border border-white/[0.08] font-mono text-xs transition-all ${
              canGoNext
                ? 'bg-white/[0.04] hover:bg-white/[0.08] text-white cursor-pointer'
                : 'opacity-30 cursor-not-allowed text-slate-600'
            }`}
            aria-label="Next month"
          >
            Next ▶
          </button>
        </div>

        {/* Calendar Days Header */}
        <div className="grid grid-cols-7 gap-1 text-center font-mono text-[11px] text-slate-400 font-semibold pb-1 border-b border-white/[0.06]">
          {DAYS_OF_WEEK.map((d, i) => (
            <div key={i}>{d}</div>
          ))}
        </div>

        {/* Calendar Days Grid */}
        <div className="grid grid-cols-7 gap-1" role="grid" aria-label="Trip calendar days">
          {calendarDays.map((item, idx) => {
            if (!item.isCurrentMonth) {
              return <div key={idx} className="h-9" />;
            }

            const isDep = depDate === item.ymd;
            const isRet = retDate === item.ymd;
            const effectiveEnd = retDate || (depDate && hoverDate && hoverDate > depDate ? hoverDate : null);
            const isInRange = Boolean(depDate && effectiveEnd && item.ymd > depDate && item.ymd < effectiveEnd);
            const isToday = item.ymd === todayYMD;

            let cellClass = 'h-9 rounded-lg font-mono text-xs flex items-center justify-center transition-all cursor-pointer select-none';

            if (item.isPast) {
              cellClass += ' opacity-25 cursor-not-allowed text-slate-600 line-through';
            } else if (isDep) {
              cellClass += ' bg-gradient-to-r from-[#24D9FF] to-[#087BD8] text-[#071D49] font-bold shadow-[0_0_12px_rgba(36,217,255,0.4)]';
            } else if (isRet) {
              cellClass += ' bg-gradient-to-r from-[#8367FF] to-[#D45CFF] text-white font-bold shadow-[0_0_12px_rgba(131,103,255,0.4)]';
            } else if (isInRange) {
              cellClass += ' bg-[#24D9FF]/15 text-[#24D9FF] border-y border-[#24D9FF]/20';
            } else {
              cellClass += ' text-[#F3F7FA] hover:bg-white/[0.08] hover:text-[#24D9FF]';
            }

            if (isToday && !isDep && !isRet && !item.isPast) {
              cellClass += ' ring-1 ring-[#24D9FF]/40';
            }

            return (
              <button
                key={idx}
                type="button"
                disabled={item.isPast}
                onClick={() => handleDayClick(item.ymd)}
                onMouseEnter={() => !item.isPast && setHoverDate(item.ymd)}
                onMouseLeave={() => setHoverDate(null)}
                className={cellClass}
                aria-label={`${item.dayNum} ${MONTH_NAMES[viewMonth]} ${viewYear}${item.isPast ? ' (Past date, unavailable)' : ''}`}
                aria-selected={isDep || isRet || isInRange}
                aria-disabled={item.isPast}
              >
                {item.dayNum}
              </button>
            );
          })}
        </div>

        {/* Modal Action Buttons */}
        <div className="pt-3 border-t border-white/[0.08] flex items-center justify-between gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.08] font-mono text-xs text-slate-300 hover:text-white transition-all cursor-pointer"
          >
            Cancel
          </button>
          <button
            onClick={handleApply}
            disabled={!depDate}
            className={`px-5 py-2.5 rounded-xl font-sans font-bold text-xs transition-all flex items-center gap-2 ${
              depDate
                ? 'bg-gradient-to-r from-[#24D9FF] via-[#8367FF] to-[#D45CFF] text-white shadow-[0_0_16px_rgba(36,217,255,0.4)] hover:opacity-95 cursor-pointer'
                : 'opacity-40 bg-white/[0.1] text-slate-400 cursor-not-allowed'
            }`}
          >
            <span>Apply Dates</span>
            <span>✓</span>
          </button>
        </div>
      </div>
    </div>
  );
};

export default DatePickerModal;
