from __future__ import annotations
import os
import io
import csv
import logging
import zipfile
from datetime import date, datetime, time, timedelta
from shared.schemas import DataQuality, Location, Money, TransportMode, TransportOption
from .base import TransportAdapter

logger = logging.getLogger(__name__)

class GTFSBusAdapter(TransportAdapter):
    """
    Parses local static GTFS bus feed files to find scheduled bus trips.
    Returns bus options flagged as estimated because the feed is a developer fixture.
    """

    def __init__(self):
        self.zip_path = os.path.join(os.path.dirname(__file__), "..", "data", "sample_bus_feed_fixture.zip")

    def _get_stop_ids(self) -> set[str]:
        try:
            if not os.path.exists(self.zip_path):
                return set()
            with zipfile.ZipFile(self.zip_path, "r") as z:
                with z.open("stops.txt") as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                    return {row["stop_id"].upper() for row in reader}
        except Exception as e:
            logger.error(f"Error reading stops.txt from GTFS zip: {e}")
            return set()

    def can_serve(self, origin: str, destination: str) -> bool:
        stops = self._get_stop_ids()
        return origin.upper() in stops and destination.upper() in stops

    def search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        num_travelers: int = 1,
        max_price: Optional[float] = None,
    ) -> list[TransportOption]:
        if not os.path.exists(self.zip_path):
            logger.warning(f"GTFS Bus feed file not found at {self.zip_path}")
            return []

        try:
            with zipfile.ZipFile(self.zip_path, "r") as z:
                # 1. Parse Agency Name
                agency_name = "Sample Test Bus Co"
                with z.open("agency.txt") as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                    for row in reader:
                        agency_name = row.get("agency_name", agency_name)
                        break

                # 2. Find active service IDs for departure_date
                weekday_map = {
                    0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday",
                    4: "friday", 5: "saturday", 6: "sunday"
                }
                target_day_name = weekday_map[departure_date.weekday()]
                date_str = departure_date.strftime("%Y%m%d")
                
                active_services = set()
                with z.open("calendar.txt") as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                    for row in reader:
                        # Check if route runs on target weekday
                        runs_on_day = row.get(target_day_name) == "1"
                        start_date = row.get("start_date")
                        end_date = row.get("end_date")
                        
                        if runs_on_day and start_date <= date_str <= end_date:
                            active_services.add(row["service_id"])

                if not active_services:
                    logger.info("No active bus services found for this date in calendar.txt")
                    return []

                # 3. Get active trip IDs
                active_trips = {} # trip_id -> trip_headsign
                with z.open("trips.txt") as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                    for row in reader:
                        if row["service_id"] in active_services:
                            active_trips[row["trip_id"]] = row.get("trip_headsign", "Local Route")

                if not active_trips:
                    logger.info("No active bus trips found for active services")
                    return []

                # 4. Filter stop times
                # We need to find stop sequences where origin appears before destination in the same trip
                trip_stop_times = {} # trip_id -> list of stop_time dicts
                with z.open("stop_times.txt") as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                    for row in reader:
                        trip_id = row["trip_id"]
                        if trip_id in active_trips:
                            if trip_id not in trip_stop_times:
                                trip_stop_times[trip_id] = []
                            trip_stop_times[trip_id].append({
                                "stop_id": row["stop_id"].upper(),
                                "stop_sequence": int(row["stop_sequence"]),
                                "arrival_time": row["arrival_time"],
                                "departure_time": row["departure_time"]
                            })

                options = []
                for trip_id, stops_list in trip_stop_times.items():
                    # Sort stop sequences
                    stops_list.sort(key=lambda x: x["stop_sequence"])
                    
                    origin_sequence = -1
                    destination_sequence = -1
                    dep_time_str = None
                    arr_time_str = None
                    
                    for s in stops_list:
                        if s["stop_id"] == origin.upper():
                            origin_sequence = s["stop_sequence"]
                            dep_time_str = s["departure_time"]
                        elif s["stop_id"] == destination.upper():
                            destination_sequence = s["stop_sequence"]
                            arr_time_str = s["arrival_time"]

                    # If origin stop is visited before destination stop
                    if 0 <= origin_sequence < destination_sequence:
                        # Parse HH:MM:SS times
                        dep_h, dep_m, dep_s = map(int, dep_time_str.split(":"))
                        arr_h, arr_m, arr_s = map(int, arr_time_str.split(":"))
                        
                        dep_time = datetime.combine(departure_date, time(dep_h, dep_m, dep_s))
                        arr_time = datetime.combine(departure_date, time(arr_h, arr_m, arr_s))
                        
                        # Handle overnight trips (though shouldn't happen on regional routes, good safety)
                        if arr_time < dep_time:
                            arr_time += timedelta(days=1)
                            
                        duration = int((arr_time - dep_time).total_seconds() / 60)

                        opt = TransportOption(
                            mode=TransportMode.bus,
                            origin=Location(name=origin.upper(), iata=origin.upper()),
                            destination=Location(name=destination.upper(), iata=destination.upper()),
                            departure_at=dep_time,
                            arrival_at=arr_time,
                            duration_minutes=duration,
                            price=Money(amount=18.50 * num_travelers, currency="GBP"),
                            carrier=agency_name,
                            booking_ref=f"GTFS-BUS-{trip_id}",
                            data_quality=DataQuality.estimated, # synthetic GTFS fixture flagged as estimated
                            is_estimated=True
                        )
                        options.append(opt)

                if max_price is not None:
                    options = [o for o in options if o.price.amount <= max_price]
                options.sort(key=lambda x: x.price.amount)
                return options

        except Exception as e:
            logger.error(f"GTFS Bus search failed: {e}")
            return []
