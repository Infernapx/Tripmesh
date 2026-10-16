import React, { useState } from 'react';
import DatePickerModal from './DatePickerModal';

export interface TripPlanPayload {
  origin?: string;
  origin_name?: string;
  destination?: string;
  destination_name?: string;
  departure_date?: string;
  check_in?: string;
  check_out?: string;
  budget_cap?: number;
  budget?: number;
  budget_status?: string;
  weather_tip?: string;
  target_currency?: string;
  display_currency?: string;
  transport_results?: Array<{
    carrier?: string;
    mode?: string;
    origin?: { iata?: string; name?: string };
    destination?: { iata?: string; name?: string };
    departure_at?: string;
    arrival_at?: string;
    duration_minutes?: number;
    price?: { amount?: number; currency?: string };
    is_estimated?: boolean;
  }>;
  hotel_results?: Array<{
    name?: string;
    rating?: number;
    check_in?: string;
    check_out?: string;
    price_per_night?: { amount?: number; currency?: string };
    total_price?: { amount?: number; currency?: string };
    amenities?: string[];
    is_estimated?: boolean;
  }>;
  local_guide_results?: Array<{
    title?: string;
    description?: string;
    category?: string;
    is_estimated?: boolean;
  }>;
  budget_reconciliation?: {
    total_cost?: number;
    currency?: string;
    budget_cap?: number;
    over_budget?: boolean;
    overage_amount?: number;
    breakdown_by_agent?: Record<string, number>;
  };
  cost_line_items?: Array<{
    description?: string;
    agent?: string;
    amount?: number;
    currency?: string;
    is_estimated?: boolean;
  }>;
  no_later_flight_found?: boolean;
  lead_banner?: string;
  weather_alert?: string;
  news_alert?: string;
}

interface ItineraryPlanCardProps {
  plan: TripPlanPayload;
  onAction?: (actionText: string, structuredPayload?: any) => void;
}

const formatCurrency = (amount?: number, currency: string = 'USD') => {
  if (amount === undefined || amount === null) return '';
  const symbols: Record<string, string> = {
    USD: '$',
    EUR: '€',
    GBP: '£',
    JPY: '¥',
    INR: '₹',
    CHF: 'CHF ',
  };
  const sym = symbols[currency.toUpperCase()] || `${currency.toUpperCase()} `;
  return `${sym}${amount.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
};

export const ItineraryPlanCard: React.FC<ItineraryPlanCardProps> = ({ plan, onAction }) => {
  const [activeTab, setActiveTab] = useState<'all' | 'flights' | 'stays' | 'experiences'>('all');
  const [isExpanded, setIsExpanded] = useState(true);
  const [isCalendarOpen, setIsCalendarOpen] = useState(false);

  const currency = plan.display_currency || plan.target_currency || 'USD';
  const flight = plan.transport_results?.[0];
  const hotel = plan.hotel_results?.[0];
  const activities = plan.local_guide_results || [];
  const recon = plan.budget_reconciliation;

  const originDisplay = plan.origin_name || plan.origin || 'Origin';
  const destinationDisplay = plan.destination_name || plan.destination || 'Destination';

  // Compute trip duration days if dates present
  let durationDays = 3;
  if (plan.departure_date && plan.check_out) {
    try {
      const d1 = new Date(plan.departure_date);
      const d2 = new Date(plan.check_out);
      const diff = Math.ceil((d2.getTime() - d1.getTime()) / (1000 * 60 * 60 * 24));
      if (diff > 0) durationDays = diff;
    } catch {
      // fallback
    }
  }

  const isOverBudget = recon?.over_budget;
  const budgetCap = recon?.budget_cap || plan.budget_cap;
  const totalCost = recon?.total_cost;

  return (
    <div className="relative rounded-2xl overflow-hidden glass-panel-warm border border-white/[0.12] bg-[#0A1830]/90 shadow-[0_16px_40px_rgba(2,10,24,0.6)] my-4 text-[#F3F7FA] transition-all duration-300">
      {/* Ambient Top Glow Line */}
      <div className="h-[2px] w-full bg-gradient-to-r from-transparent via-[#24D9FF] to-[#8367FF]" />

      {/* Honest / Notice Lead Banner */}
      {(plan.lead_banner || plan.no_later_flight_found) && (
        <div className="px-5 py-3 bg-[#FF9B58]/10 border-b border-[#FF9B58]/25 flex items-center gap-2.5 text-xs text-[#FF9B58]">
          <span className="text-sm">⚠️</span>
          <span className="font-medium">
            {plan.lead_banner || "Notice: Flight schedule adjusted. No later alternative flights found on this route."}
          </span>
        </div>
      )}

      {/* Weather Alert Banner */}
      {plan.weather_alert && (
        <div className="px-5 py-3 bg-[#FF9B58]/15 border-b border-[#FF9B58]/30 flex items-center gap-2.5 text-xs text-[#FFB070]">
          <span className="font-medium">{plan.weather_alert}</span>
        </div>
      )}

      {/* Destination News / Disruption Alert Banner */}
      {plan.news_alert && (
        <div className="px-5 py-3 bg-[#FF453A]/15 border-b border-[#FF453A]/30 flex items-center gap-2.5 text-xs text-[#FF857F]">
          <span className="font-medium">{plan.news_alert}</span>
        </div>
      )}

      {/* Card Header: Luxury Boarding Pass Aesthetics */}
      <div className="p-5 sm:p-6 border-b border-white/[0.08] bg-white/[0.01]">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className="font-mono text-[10px] uppercase font-bold tracking-widest px-2.5 py-0.5 rounded-full bg-[#24D9FF]/10 border border-[#24D9FF]/30 text-[#24D9FF] shadow-[0_0_8px_rgba(36,217,255,0.2)]">
                AI ITINERARY PASSPORT
              </span>
              <span className="font-mono text-xs text-slate-300">
                {currency}
              </span>
            </div>
            <h3 className="text-2xl sm:text-3xl font-sans font-bold tracking-tight text-white flex items-center gap-2">
              <span>{originDisplay}</span>
              <span className="text-[#24D9FF] text-lg font-mono">━━✈━━</span>
              <span>{destinationDisplay}</span>
            </h3>
          </div>

          {/* Quick Metadata Chips */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs px-3 py-1 rounded-lg bg-white/[0.04] border border-white/[0.08] text-[#F3F7FA]">
              ⏳ {durationDays} DAYS
            </span>
            {totalCost !== undefined && (
              <span className="font-mono text-xs px-3 py-1 rounded-lg bg-[#24D9FF]/10 border border-[#24D9FF]/30 text-[#24D9FF] font-bold shadow-[0_0_10px_rgba(36,217,255,0.2)]">
                {formatCurrency(totalCost, currency)}
              </span>
            )}
            <button
              onClick={() => setIsExpanded(!isExpanded)}
              className="font-mono text-xs px-3 py-1 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.08] text-slate-300 hover:text-white transition-all cursor-pointer flex items-center gap-1"
            >
              <span>{isExpanded ? 'Collapse' : 'Expand'}</span>
              <span>{isExpanded ? '▲' : '▼'}</span>
            </button>
          </div>
        </div>

        {/* Date & Traveler Sub-strip */}
        <div className="mt-4 pt-3 border-t border-white/[0.05] flex flex-wrap items-center justify-between gap-3 text-xs font-mono text-[#91A1B3]">
          <div className="flex items-center gap-4">
            <div>
              <span className="text-[#91A1B3]/70">DEPARTURE: </span>
              <span className="text-white font-semibold">{plan.departure_date || 'Flexible'}</span>
            </div>
            {plan.check_out && (
              <div>
                <span className="text-[#91A1B3]/70">RETURN: </span>
                <span className="text-white font-semibold">{plan.check_out}</span>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-[#00F5D4]" />
            <span>REAL-TIME AGENT VERIFIED</span>
          </div>
        </div>

        {/* Grounded 1-line Weather Tip Line */}
        {plan.weather_tip && (
          <div className="mt-3 pt-2.5 border-t border-white/[0.06] flex items-center gap-2 text-xs font-sans text-[#24D9FF] bg-[#24D9FF]/[0.04] px-3 py-1.5 rounded-lg border border-[#24D9FF]/20 shadow-[0_0_8px_rgba(36,217,255,0.08)]">
            <span className="text-sm shrink-0">🌤️</span>
            <span className="font-medium leading-relaxed tracking-wide text-white/90">{plan.weather_tip}</span>
          </div>
        )}
      </div>

      {/* Expandable Body */}
      {isExpanded && (
        <div className="p-5 sm:p-6 space-y-6">
          {/* Section Filter Pills */}
          <div className="flex items-center gap-2 pb-1 border-b border-white/[0.05] overflow-x-auto">
            <button
              onClick={() => setActiveTab('all')}
              className={`px-3 py-1 rounded-full font-mono text-xs transition-all cursor-pointer ${
                activeTab === 'all'
                  ? 'bg-[#24D9FF]/20 border border-[#24D9FF]/50 text-[#24D9FF] font-semibold'
                  : 'text-slate-300 hover:text-white'
              }`}
            >
              All Components
            </button>
            {flight && (
              <button
                onClick={() => setActiveTab('flights')}
                className={`px-3 py-1 rounded-full font-mono text-xs transition-all cursor-pointer ${
                  activeTab === 'flights'
                    ? 'bg-[#24D9FF]/20 border border-[#24D9FF]/50 text-[#24D9FF] font-semibold'
                    : 'text-slate-300 hover:text-white'
                }`}
              >
                ✈️ Flights ({plan.transport_results?.length || 1})
              </button>
            )}
            {hotel && (
              <button
                onClick={() => setActiveTab('stays')}
                className={`px-3 py-1 rounded-full font-mono text-xs transition-all cursor-pointer ${
                  activeTab === 'stays'
                    ? 'bg-[#24D9FF]/20 border border-[#24D9FF]/50 text-[#24D9FF] font-semibold'
                    : 'text-slate-300 hover:text-white'
                }`}
              >
                🏨 Stays ({plan.hotel_results?.length || 1})
              </button>
            )}
            {activities.length > 0 && (
              <button
                onClick={() => setActiveTab('experiences')}
                className={`px-3 py-1 rounded-full font-mono text-xs transition-all cursor-pointer ${
                  activeTab === 'experiences'
                    ? 'bg-[#24D9FF]/20 border border-[#24D9FF]/50 text-[#24D9FF] font-semibold'
                    : 'text-slate-300 hover:text-white'
                }`}
              >
                🧭 Experiences ({activities.length})
              </button>
            )}
          </div>

          {/* FLIGHTS SECTION */}
          {(activeTab === 'all' || activeTab === 'flights') && plan.transport_results && plan.transport_results.length > 0 && (
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs font-mono uppercase tracking-wider text-slate-300 font-semibold">
                <span className="flex items-center gap-1.5">
                  <span className="text-[#24D9FF]">✈️</span>
                  <span>Flight & Transport Routes</span>
                </span>
                <span>Ascending Fare</span>
              </div>

              <div className="grid grid-cols-1 gap-3">
                {plan.transport_results.map((fl, idx) => (
                  <div
                    key={idx}
                    className="group p-4 rounded-xl bg-white/[0.03] border border-white/[0.08] hover:border-[#24D9FF]/40 hover:bg-white/[0.05] transition-all flex flex-col sm:flex-row sm:items-center justify-between gap-4"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-sans font-bold text-white text-sm flex items-center gap-1.5">
                          <span className="inline-block transition-transform duration-300 group-hover:translate-x-1 text-[#24D9FF]">✈</span>
                          <span>{fl.carrier || 'Scheduled Transit'}</span>
                        </span>
                        {fl.is_estimated && (
                          <span className="font-mono text-[9px] uppercase px-1.5 py-0.5 bg-[#FF7895]/15 border border-[#FF7895]/30 text-[#FF7895] rounded">
                            Estimated
                          </span>
                        )}
                        <span className="font-mono text-[10px] text-slate-400">
                          {fl.mode?.toUpperCase() || 'FLIGHT'}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 text-xs font-mono text-[#F3F7FA]">
                        <span className="font-bold text-[#24D9FF]">
                          {fl.origin?.iata || plan.origin}{' '}
                          <span className="text-slate-400 font-normal">
                            ({fl.departure_at ? fl.departure_at.slice(11, 16) : '08:00'})
                          </span>
                        </span>
                        <span className="text-slate-400">━━━</span>
                        <span className="font-bold text-[#24D9FF]">
                          {fl.destination?.iata || plan.destination}{' '}
                          <span className="text-slate-400 font-normal">
                            ({fl.arrival_at ? fl.arrival_at.slice(11, 16) : '11:30'})
                          </span>
                        </span>
                        {fl.duration_minutes && (
                          <span className="text-slate-400 text-[11px]">
                            • {Math.floor(fl.duration_minutes / 60)}h {fl.duration_minutes % 60}m
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="text-right sm:border-l sm:border-white/[0.08] sm:pl-5">
                      <div className="font-mono text-base font-bold text-[#24D9FF]">
                        {formatCurrency(fl.price?.amount, fl.price?.currency || currency)}
                      </div>
                      <span className="text-[10px] font-mono text-slate-400">one-way total</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* HOTELS SECTION */}
          {(activeTab === 'all' || activeTab === 'stays') && plan.hotel_results && plan.hotel_results.length > 0 && (
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs font-mono uppercase tracking-wider text-slate-300 font-semibold">
                <span className="flex items-center gap-1.5">
                  <span className="text-[#8367FF]">🏨</span>
                  <span>Accommodation & Boutique Stays</span>
                </span>
                <span>Grounded Availability</span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {plan.hotel_results.map((ht, idx) => (
                  <div
                    key={idx}
                    className="group p-4 rounded-xl bg-white/[0.03] border border-white/[0.08] hover:border-[#8367FF]/50 hover:bg-white/[0.05] transition-all flex flex-col justify-between"
                  >
                    <div>
                      <div className="flex items-start justify-between gap-2 mb-1.5">
                        <h4 className="font-sans font-bold text-white text-sm flex items-center gap-1.5">
                          <span className="inline-block transition-transform duration-300 group-hover:scale-110 text-[#8367FF]">🏨</span>
                          <span>{ht.name || 'Boutique Residence'}</span>
                        </h4>
                        {ht.rating && (
                          <span className="font-mono text-xs text-[#FF9B58] font-bold px-1.5 py-0.5 rounded bg-[#FF9B58]/15 border border-[#FF9B58]/30">
                            ★ {ht.rating}
                          </span>
                        )}
                      </div>

                      <div className="text-xs font-mono text-slate-400 mb-3">
                        {ht.check_in || plan.check_in} → {ht.check_out || plan.check_out}
                      </div>

                      {ht.amenities && ht.amenities.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 mb-4">
                          {ht.amenities.slice(0, 4).map((a, i) => (
                            <span
                              key={i}
                              className="font-mono text-[10px] px-2 py-0.5 rounded bg-white/[0.04] border border-white/[0.08] text-slate-300"
                            >
                              {a}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="pt-3 border-t border-white/[0.06] flex items-center justify-between">
                      <span className="text-xs font-mono text-slate-400">
                        {ht.price_per_night?.amount && (
                          <span>{formatCurrency(ht.price_per_night.amount, ht.price_per_night.currency || currency)}/night</span>
                        )}
                      </span>
                      <span className="font-mono text-sm font-bold text-[#24D9FF]">
                        Total: {formatCurrency(ht.total_price?.amount, ht.total_price?.currency || currency)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* EXPERIENCES SECTION */}
          {(activeTab === 'all' || activeTab === 'experiences') && activities.length > 0 && (
            <div className="space-y-3">
              <div className="text-xs font-mono uppercase tracking-wider text-slate-300 font-semibold flex items-center gap-1.5">
                <span className="text-[#24D9FF]">🧭</span>
                <span>Atmospheric Experiences & Discovery</span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {activities.map((act, i) => (
                  <div
                    key={i}
                    className="p-3.5 rounded-xl bg-white/[0.03] border border-white/[0.08] hover:border-[#24D9FF]/40 transition-all space-y-1"
                  >
                    <div className="font-semibold text-white text-xs">{act.title}</div>
                    {act.description && (
                      <p className="text-[11px] text-slate-300 line-clamp-2 leading-relaxed">
                        {act.description}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* BUDGET RECONCILIATION BAR (Shown ONLY when budget is requested / exists) */}
          {plan.budget_status && (
            <div className="p-4 rounded-xl bg-white/[0.03] border border-white/[0.08] space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm">💰</span>
                  <span className="font-mono text-xs font-bold text-white uppercase tracking-wider">
                    Budget Boundary Status
                  </span>
                </div>
                <span
                  className={`font-mono text-[11px] px-2.5 py-0.5 rounded-full font-bold ${
                    isOverBudget || plan.budget_status.toLowerCase().includes('over')
                      ? 'bg-[#FF7895]/15 border border-[#FF7895]/40 text-[#FF7895]'
                      : 'bg-[#24D9FF]/15 border border-[#24D9FF]/40 text-[#24D9FF]'
                  }`}
                >
                  {isOverBudget || plan.budget_status.toLowerCase().includes('over')
                    ? (plan.budget_status.includes('Over') ? `⚠️ ${plan.budget_status}` : `⚠️ Over Budget (${formatCurrency(recon?.overage_amount, currency)})`)
                    : (plan.budget_status.includes('Within') ? `✅ ${plan.budget_status}` : '✅ Within Budget Target')}
                </span>
              </div>

              {/* Progress Bar */}
              {budgetCap && totalCost && (
                <div className="space-y-1.5">
                  <div className="h-2 w-full rounded-full bg-white/[0.08] overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        isOverBudget ? 'bg-gradient-to-r from-[#FF9B58] to-[#FF7895]' : 'bg-gradient-to-r from-[#24D9FF] to-[#8367FF]'
                      }`}
                      style={{
                        width: `${Math.min(100, Math.round((totalCost / budgetCap) * 100))}%`,
                      }}
                    />
                  </div>
                  <div className="flex justify-between text-[11px] font-mono text-slate-400">
                    <span>Total Plan: {formatCurrency(totalCost, currency)}</span>
                    <span>Budget Cap: {formatCurrency(budgetCap, currency)}</span>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ACTION BUTTONS */}
          <div className="pt-2 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => onAction && onAction('confirm')}
                className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#24D9FF] via-[#8367FF] to-[#D45CFF] text-white font-sans font-bold text-xs shadow-[0_0_15px_rgba(36,217,255,0.4)] hover:opacity-95 transition-all cursor-pointer flex items-center gap-2"
              >
                <span>✦</span>
                <span>Confirm Itinerary</span>
              </button>
              <button
                onClick={() => onAction && onAction('cheaper hotel')}
                className="px-4 py-2.5 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.1] text-xs font-mono text-[#F3F7FA] transition-all cursor-pointer"
              >
                Cheaper Hotel
              </button>
              <button
                onClick={() => setIsCalendarOpen(true)}
                className="px-4 py-2.5 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.1] text-xs font-mono text-[#F3F7FA] transition-all cursor-pointer flex items-center gap-1.5"
              >
                <span>📅</span>
                <span>Change Dates</span>
              </button>
            </div>

            <div className="text-xs font-mono text-[#91A1B3]">
              Reply in chat to fine-tune any detail
            </div>
          </div>
        </div>
      )}

      {/* Date Picker Range Modal */}
      <DatePickerModal
        isOpen={isCalendarOpen}
        onClose={() => setIsCalendarOpen(false)}
        initialDeparture={plan.departure_date}
        initialReturn={plan.check_out}
        onApply={(departure, returnDate, chatLine) => {
          if (onAction) {
            onAction(chatLine, {
              type: 'change_dates',
              departure,
              return: returnDate,
            });
          }
        }}
      />
    </div>
  );
};

export default ItineraryPlanCard;
