import logging

try:
    from .tools.hotel_api import search_hotels_api
except ImportError:
    from app.tools.hotel_api import search_hotels_api

logger = logging.getLogger(__name__)

try:
    from crewai import Agent, Task, Crew
except ImportError:
    # Python 3.14 compatibility stub for CrewAI when packages/numpy fail to compile on Windows
    logger.warning("CrewAI package not imported. Employing local CrewAI compatibility stub.")
    
    class Agent:
        def __init__(self, role, goal, backstory, tools=None, verbose=True, allow_delegation=False, llm=None):
            self.role = role
            self.goal = goal
            self.backstory = backstory
            self.tools = tools or []

    class Task:
        def __init__(self, description, expected_output, agent, tools=None):
            self.description = description
            self.expected_output = expected_output
            self.agent = agent
            self.tools = tools or []

    class Crew:
        def __init__(self, agents, tasks, verbose=True):
            self.agents = agents
            self.tasks = tasks
        
        async def kickoff_async(self, inputs):
            from datetime import date, timedelta
            def_in = (date.today() + timedelta(days=14)).isoformat()
            def_out = (date.today() + timedelta(days=16)).isoformat()
            destination = inputs.get("destination", "LIS")
            check_in = inputs.get("check_in") or def_in
            check_out = inputs.get("check_out") or def_out
            num_travelers = int(inputs.get("num_travelers", 1))
            price_ceiling = inputs.get("price_ceiling")
            excluded_hotels = inputs.get("excluded_hotels")
            prefer_cheaper = inputs.get("prefer_cheaper", False)
            return await search_hotels_api(destination, check_in, check_out, num_travelers, price_ceiling=price_ceiling, excluded_hotels=excluded_hotels, prefer_cheaper=prefer_cheaper)
            
        def kickoff(self, inputs):
            from datetime import date, timedelta
            def_in = (date.today() + timedelta(days=14)).isoformat()
            def_out = (date.today() + timedelta(days=16)).isoformat()
            destination = inputs.get("destination", "LIS")
            check_in = inputs.get("check_in") or def_in
            check_out = inputs.get("check_out") or def_out
            num_travelers = int(inputs.get("num_travelers", 1))
            price_ceiling = inputs.get("price_ceiling")
            excluded_hotels = inputs.get("excluded_hotels")
            prefer_cheaper = inputs.get("prefer_cheaper", False)
            
            import asyncio
            try:
                asyncio.get_running_loop()
                from hotel_agent_crewai.app.tools.hotel_api import _get_mock_hotels
                return _get_mock_hotels(destination, check_in, check_out, num_travelers, price_ceiling=price_ceiling, excluded_hotels=excluded_hotels, prefer_cheaper=prefer_cheaper)
            except RuntimeError:
                return asyncio.run(search_hotels_api(destination, check_in, check_out, num_travelers, price_ceiling=price_ceiling, excluded_hotels=excluded_hotels, prefer_cheaper=prefer_cheaper))

# Define Agent
hotel_expert = Agent(
    role="Hotel Reservations Expert",
    goal="Find hotel options in a specific city for a given date range",
    backstory="You are an expert travel concierge specializing in locating premium hotel properties with rich amenities.",
    tools=[search_hotels_api],
    verbose=True
)

# Define Task
find_hotels_task = Task(
    description="Locate hotel properties in {destination} from {check_in} to {check_out} for {num_travelers} travelers.",
    expected_output="A list of structured accommodation options with rates, locations, ratings, and amenities.",
    agent=hotel_expert
)

# Define Crew
hotel_crew = Crew(
    agents=[hotel_expert],
    tasks=[find_hotels_task],
    verbose=True
)
