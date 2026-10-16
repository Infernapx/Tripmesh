"""
planner_agent/app/export_service.py
-----------------------------------
Generates PDF and iCalendar (.ics) export files for confirmed trip itineraries.
- PDF: Built with reportlab with day-by-day itinerary, cost breakdown, and prominent SIMULATED disclaimer.
- .ICS: Built with icalendar with timed transport, hotel date spans, and local guide events.
"""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from typing import Any

from icalendar import Calendar, Event, vDatetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from shared.resolvers import get_default_trip_dates


def generate_itinerary_pdf(plan_data: dict[str, Any]) -> bytes:
    """
    Generates a beautifully styled, downloadable PDF itinerary for a confirmed trip.
    Includes a clear disclaimer that it is a simulated booking.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        "TripTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#0f172a"),
    )
    
    subtitle_style = ParagraphStyle(
        "TripSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#475569"),
    )
    
    section_heading = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#0369a1"),
        spaceBefore=12,
        spaceAfter=6,
    )
    
    disclaimer_style = ParagraphStyle(
        "Disclaimer",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#dc2626"),
        alignment=1, # Centered
    )
    
    body_style = ParagraphStyle(
        "BodyText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#1e293b"),
    )

    elements = []

    # 1. Header & Title
    origin_name = plan_data.get("origin_name") or plan_data.get("origin", "Origin")
    dest_name = plan_data.get("destination_name") or plan_data.get("destination", "Destination")
    elements.append(Paragraph(f"TripMesh Itinerary: {origin_name} to {dest_name}", title_style))
    
    def_dep, def_cin, def_cout = get_default_trip_dates()
    dep_date = plan_data.get("departure_date") or def_dep
    check_out = plan_data.get("check_out") or def_cout
    confirmed_at = plan_data.get("confirmed_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    
    elements.append(Paragraph(f"Travel Dates: <b>{dep_date}</b> to <b>{check_out}</b> | Confirmed: <b>{confirmed_at}</b>", subtitle_style))
    elements.append(Spacer(1, 10))

    # 2. Prominent Simulated Disclaimer Banner
    disclaimer_box = [
        [Paragraph("⚠️ <b>SIMULATED BOOKING — NOT A REAL TICKET OR BOOKING CONFIRMATION</b><br/>"
                   "<font size=8 color='#7f1d1d'>Generated for demonstration & travel planning purposes by TripMesh Multi-Agent System.</font>", disclaimer_style)]
    ]
    t_disc = Table(disclaimer_box, colWidths=[540])
    t_disc.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#fee2e2")),
        ('BORDER', (0, 0), (-1, -1), 1, colors.HexColor("#ef4444")),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(t_disc)
    elements.append(Spacer(1, 12))

    # 3. Cost & Budget Breakdown
    recon = plan_data.get("budget_reconciliation") or {}
    currency = plan_data.get("display_currency") or plan_data.get("target_currency") or "USD"
    cost_items = plan_data.get("cost_line_items") or []
    
    elements.append(Paragraph("💰 Cost & Budget Breakdown", section_heading))
    
    table_data = [["Category", "Item / Description", "Data Source", f"Amount ({currency})"]]
    total_cost = 0.0
    for item in cost_items:
        src = item.get("source_agent", "general").title()
        desc = item.get("description", "Service")
        amt = float(item.get("amount", 0.0))
        total_cost += amt
        quality = "Estimated" if item.get("is_estimated") else "Live"
        table_data.append([src, desc, quality, f"{amt:.2f} {item.get('currency', currency)}"])
        
    table_data.append(["TOTAL", "Consolidated Estimated Itinerary Cost", "", f"{total_cost:.2f} {currency}"])
    
    cost_table = Table(table_data, colWidths=[90, 250, 90, 110])
    cost_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
        ('TOPPADDING', (0, 0), (-1, 0), 5),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
        ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
    ]))
    elements.append(cost_table)
    elements.append(Spacer(1, 14))

    # 4. Transport Section
    transport_results = plan_data.get("transport_results") or []
    if transport_results:
        elements.append(Paragraph("✈️ Selected Transport Options", section_heading))
        for t in transport_results[:3]:
            carrier = t.get("carrier", "Carrier")
            dep_at = t.get("departure_at", "")
            arr_at = t.get("arrival_at", "")
            dur = t.get("duration_minutes", 0)
            orig = t.get("origin", {}).get("iata", "ORIG")
            dest = t.get("destination", {}).get("iata", "DEST")
            price_info = f"{t.get('price', {}).get('amount')} {t.get('price', {}).get('currency')}"
            quality_tag = " [Estimated Fare]" if t.get("is_estimated") else " [Live Fare]"
            
            p_text = f"• <b>{carrier}</b> ({orig} ➔ {dest}) | Departs: <b>{dep_at}</b> | Arrives: <b>{arr_at}</b> ({dur}m)<br/>" \
                     f"  Price: <b>{price_info}</b>{quality_tag} | Booking Ref: <code>{t.get('booking_ref', 'N/A')}</code>"
            elements.append(Paragraph(p_text, body_style))
            elements.append(Spacer(1, 4))
        elements.append(Spacer(1, 8))

    # 5. Hotel Section
    hotel_results = plan_data.get("hotel_results") or []
    if hotel_results:
        elements.append(Paragraph("🏨 Selected Accommodation", section_heading))
        for h in hotel_results[:2]:
            h_name = h.get("name", "Hotel")
            rating = f"{h.get('rating', '4.0')}⭐"
            c_in = h.get("check_in", dep_date)
            c_out = h.get("check_out", check_out)
            tot_price = f"{h.get('total_price', {}).get('amount')} {h.get('total_price', {}).get('currency')}"
            amenities = ", ".join(h.get("amenities", ["WiFi"]))
            
            h_text = f"• <b>{h_name}</b> ({rating}) | Check-in: <b>{c_in}</b> | Check-out: <b>{c_out}</b><br/>" \
                     f"  Total Cost: <b>{tot_price}</b> | Amenities: {amenities}"
            elements.append(Paragraph(h_text, body_style))
            elements.append(Spacer(1, 4))
        elements.append(Spacer(1, 8))

    # 6. Activities & Guide Section
    guide_results = plan_data.get("local_guide_results") or []
    if guide_results:
        elements.append(Paragraph("🏛️ Recommended Activities & Points of Interest", section_heading))
        for idx, act in enumerate(guide_results[:4], 1):
            name = act.get("name", "Activity")
            dur = f"{act.get('estimated_duration_hours')} hrs" if act.get("estimated_duration_hours") else "Flexible"
            cost_val = act.get("estimated_cost", {}).get("amount", 0)
            cost_str = f"{cost_val} {act.get('estimated_cost', {}).get('currency', 'USD')}" if cost_val > 0 else "Free / Public"
            desc = act.get("description", "")
            cite = act.get("source_citation", "Wikivoyage")
            
            act_text = f"<b>{idx}. {name}</b> ({dur} | {cost_str})<br/>" \
                       f"   {desc}<br/>" \
                       f"   <font size=8 color='#64748b'><i>Source: {cite}</i></font>"
            elements.append(Paragraph(act_text, body_style))
            elements.append(Spacer(1, 5))

    # Footer
    elements.append(Spacer(1, 15))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#94a3b8"), spaceAfter=5))
    elements.append(Paragraph("<font size=8 color='#94a3b8'>TripMesh AI Multi-Agent Travel Planner • Simulating Autonomous A2A Travel Orchestration</font>", body_style))

    doc.build(elements)
    return buffer.getvalue()


def generate_itinerary_ics(plan_data: dict[str, Any]) -> bytes:
    """
    Generates a standard RFC 5545 iCalendar (.ics) file with timed transport,
    hotel stays, and activity events.
    """
    cal = Calendar()
    cal.add("prodid", "-//TripMesh Travel Planner//tripmesh.ai//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")

    now = datetime.now(timezone.utc)
    def_dep, def_cin, def_cout = get_default_trip_dates()
    dep_date_str = plan_data.get("departure_date") or def_dep
    try:
        base_date = date.fromisoformat(dep_date_str)
    except Exception:
        base_date = date.fromisoformat(def_dep)

    # 1. Transport Event
    transport_results = plan_data.get("transport_results") or []
    if transport_results:
        best_t = transport_results[0]
        event = Event()
        carrier = best_t.get("carrier", "Flight/Transit")
        orig = best_t.get("origin", {}).get("iata", "ORIG")
        dest = best_t.get("destination", {}).get("iata", "DEST")
        
        event.add("summary", f"Travel: {carrier} ({orig} -> {dest}) [SIMULATED]")
        
        dep_str = best_t.get("departure_at")
        arr_str = best_t.get("arrival_at")
        try:
            dep_dt = datetime.fromisoformat(dep_str)
            arr_dt = datetime.fromisoformat(arr_str)
        except Exception:
            dep_dt = datetime.combine(base_date, datetime.min.time().replace(hour=8))
            arr_dt = dep_dt + timedelta(hours=3)
            
        event.add("dtstart", dep_dt)
        event.add("dtend", arr_dt)
        event.add("dtstamp", now)
        event.add("uid", f"tripmesh-transport-{best_t.get('booking_ref', 'ref1')}@tripmesh.ai")
        
        price = f"{best_t.get('price', {}).get('amount')} {best_t.get('price', {}).get('currency')}"
        event.add("description", f"Carrier: {carrier}\nPrice: {price}\nBooking Ref: {best_t.get('booking_ref')}\nQuality: {'Estimated' if best_t.get('is_estimated') else 'Live'}\nNOTE: SIMULATED BOOKING - Not a real ticket.")
        event.add("location", f"{orig} Airport")
        cal.add_component(event)

    # 2. Hotel Event
    hotel_results = plan_data.get("hotel_results") or []
    if hotel_results:
        best_h = hotel_results[0]
        event = Event()
        h_name = best_h.get("name", "Hotel Accommodation")
        
        event.add("summary", f"Hotel Stay: {h_name} [SIMULATED]")
        
        c_in_str = best_h.get("check_in", dep_date_str)
        c_out_str = best_h.get("check_out") or def_cout
        try:
            c_in = date.fromisoformat(c_in_str)
            c_out = date.fromisoformat(c_out_str)
        except Exception:
            c_in = base_date
            c_out = base_date + timedelta(days=2)
            
        event.add("dtstart", c_in)
        event.add("dtend", c_out)
        event.add("dtstamp", now)
        event.add("uid", f"tripmesh-hotel-{best_h.get('booking_ref', 'hotel1')}@tripmesh.ai")
        
        tot_price = f"{best_h.get('total_price', {}).get('amount')} {best_h.get('total_price', {}).get('currency')}"
        event.add("description", f"Hotel: {h_name}\nRating: {best_h.get('rating', '4.0')} Stars\nTotal: {tot_price}\nAmenities: {', '.join(best_h.get('amenities', []))}\nNOTE: SIMULATED BOOKING.")
        event.add("location", best_h.get("location", {}).get("name", "Destination"))
        cal.add_component(event)

    # 3. Activities Events
    guide_results = plan_data.get("local_guide_results") or []
    for idx, act in enumerate(guide_results[:3], 1):
        event = Event()
        name = act.get("name", "Activity")
        event.add("summary", f"Activity: {name} [SIMULATED]")
        
        act_date = base_date + timedelta(days=1)
        start_dt = datetime.combine(act_date, datetime.min.time().replace(hour=10 + idx * 2))
        dur_h = int(act.get("estimated_duration_hours") or 2)
        end_dt = start_dt + timedelta(hours=dur_h)
        
        event.add("dtstart", start_dt)
        event.add("dtend", end_dt)
        event.add("dtstamp", now)
        event.add("uid", f"tripmesh-activity-{idx}-{base_date}@tripmesh.ai")
        
        event.add("description", f"{act.get('description', '')}\nSource: {act.get('source_citation', 'Wikivoyage')}\nNOTE: SIMULATED ITINERARY.")
        event.add("location", act.get("location", {}).get("name", "Local Attraction"))
        cal.add_component(event)

    return cal.to_ical()
