"""scenario 阶段（场景生成）的多 agent 提示词。

生成（generator）与分类（classifier）直接复用原版 awm/prompts.py 的提示词，
这里只新增评审（reviewer）提示词——评审结果带 issue，支持「带意见重生成」。
"""

# 评审：判断场景是否适合 API 仿真 + 是否与已有场景重复/笼统，输出逐场景 verdicts。
SCENARIO_REVIEW_SYSTEM_PROMPT = """You are a reviewer for website/app scenarios in an API-synthesis pipeline.

Each candidate scenario will be turned into a simulated REST API + SQLite database that an agent interacts with via CRUD operations.

IMPORTANT: duplicate / near-duplicate detection is ALREADY handled by a separate embedding-based step before you see these candidates — do NOT reject for being similar to an existing scenario. Only judge the TWO things below:

1. SUITABILITY: Can the platform's core value be synthesized as CRUD operations on structured data?
   - GOOD: e-commerce, booking, task/project management, banking, CRM, inventory, scheduling, HR, logistics, fitness tracking, healthcare, real estate, education, etc.
   - BAD (reject): content-heavy sites (news/articles/videos), search engines, AI assistants/chatbots, translation services, or anything whose core value is non-synthesizable content / real-time external data / ML inference.

2. CONCRETENESS: Does the scenario mention at least one concrete entity or workflow?
   - KEEP if it names at least 1-2 concrete entities or workflows (e.g. products/orders, tasks/boards, appointments, accounts/transactions). You do NOT need every detail spelled out.
   - Reject ONLY if it is truly empty/generic (e.g. just "a social media app" with no entities or workflows at all).

Be GENEROUS: only reject clear unsuitability (content-heavy / non-synthesizable) or truly empty descriptions. If in doubt, KEEP.

Return ONLY JSON (no markdown) in this format:
{
  "results": [
    {"name": "Platform Name", "pass": true, "issue": ""},
    {"name": "Another Platform", "pass": false, "issue": "too similar to existing 'Trello': both are generic project-management boards"}
  ]
}

The "results" array must contain one entry for EVERY candidate. When pass=false, give a SPECIFIC, actionable issue (e.g. "switch to a different industry", "change the core workflow", "add concrete entities like ...") so the generator can fix it."""


SCENARIO_REVIEW_USER_PROMPT = """## Existing Scenarios (already in dataset):
{existing_scenarios}

## Candidate Scenarios to Review:
{candidates}

For each candidate, decide keep/reject based on suitability + diversity + concreteness, and return a verdict with an actionable issue when rejected."""



# ==== 从 lib/prompts.py 合并进来的常量 ====
SCENARIO_GENERATION_SYSTEM_PROMPT = """You are an expert at identifying websites, apps, and digital platforms that are HIGHLY SUITABLE for API environment simulation and database-driven interactions.

## KEY PRINCIPLE: Can the data be SYNTHESIZED?

We need platforms where data can be FAKED but still be REALISTIC and USEFUL.

### Data that CAN be synthesized:
- Numbers: temperatures, prices, quantities, ratings, coordinates, stock prices
- Entities: users, products, orders, bookings, employees, devices, sensors
- Status values: order status, flight status, device state
- Short text: names, titles, addresses, categories, descriptions
- Timestamps: dates, schedules, deadlines, historical records
- Geographic: cities, airports, coordinates (static data)

### Data that CANNOT be synthesized meaningfully:
- Long articles: news content, blog posts, encyclopedia entries
- Media: actual video/audio content (not metadata)
- AI inference: chatbot responses, ML-based recommendations
- Real search: search engine results with ranking

## HIGH Suitability Examples (GENERATE THESE):

| Platform Type | Why Suitable | Key Entities (all synthesizable) |
|---------------|--------------|----------------------------------|
| E-commerce | CRUD on orders, reviews | products, orders, cart, reviews |
| Task Management | CRUD on tasks | tasks, boards, assignments |
| Banking/Fintech | CRUD on transactions | accounts, transactions, transfers |
| Booking/Reservation | CRUD on reservations | listings, bookings, availability |
| Weather Services | Query weather data | locations, forecasts, alerts, history |
| Flight/Travel | Search & book flights | flights, airports, bookings, prices |
| Stock Trading | Trade & portfolio | stocks, trades, portfolio, prices |
| IoT/Smart Home | Control devices | devices, sensors, readings, schedules |
| Fitness Tracking | Log workouts | workouts, exercises, metrics, goals |
| Healthcare | Manage appointments | patients, appointments, records, prescriptions |
| HR/Payroll | Manage employees | employees, timesheets, payroll |
| CRM/Sales | Manage leads | leads, deals, contacts, activities |
| Inventory | Manage stock | products, inventory, warehouses |
| Logistics | Track shipments | shipments, packages, routes, status |
| Restaurant/Food | Orders & menu | menu_items, orders, reservations |
| Property/Real Estate | Listings & viewings | properties, listings, viewings, offers |
| Education/LMS | Courses & grades | courses, enrollments, assignments, grades |
| Event Management | Events & tickets | events, tickets, attendees, venues |

## LOW Suitability (AVOID THESE):

| Platform Type | Why NOT Suitable | Problem |
|---------------|------------------|---------|
| News sites (BBC, CNN) | Article CONTENT is the product | Cannot synthesize meaningful articles |
| Wikipedia/Encyclopedia | Article CONTENT is the product | Cannot fake knowledge |
| Search Engines | Need real search index + ranking | Cannot simulate relevance |
| AI Assistants (ChatGPT) | Need real AI inference | Cannot fake AI responses |
| Translation (Google Translate) | Need real NLP models | Cannot fake translations |
| Content platforms focus | If CONTENT is core value | Metadata is not enough |

## MEDIUM Suitability (Focus on CRUD aspects):

| Platform | What CAN be simulated | What CANNOT |
|----------|----------------------|-------------|
| YouTube | Playlists, subs, comments, channel mgmt | Actual videos, recommendations |
| Spotify | Playlists, library, following | Actual audio, music discovery |
| Reddit | Posts (short), comments, votes | Long-form quality content |

## Categories to Cover (for diversity):

E-commerce, Booking/Reservation, Task/Project Management, Finance/Banking,
Weather/Environmental, Flight/Travel, Stock/Investment, IoT/Smart Home,
Fitness/Health Tracking, Healthcare/Medical, HR/Recruiting, CRM/Sales,
Inventory/Warehouse, Logistics/Shipping, Restaurant/Food, Real Estate,
Education/LMS, Event/Ticketing, Legal/Contracts, Subscription Management,
Customer Support, Forms/Surveys, Analytics Dashboards, Fleet Management,
Utility Management (electricity, water), Insurance, Pet Services, Automotive

## Your Task:
Generate NEW platforms that are HIGHLY SUITABLE - where data can be realistically SYNTHESIZED and users perform meaningful CRUD operations."""

SCENARIO_GENERATION_USER_PROMPT_TEMPLATE = """Here are {num_examples} examples of suitable website/app scenarios:

{examples}

---

Now generate {num_to_generate} NEW and DIVERSE website/app/platform scenarios that are DIFFERENT from all examples above.

Requirements:
1. Each scenario must be suitable for API environment synthesis (CRUD operations, database interactions)
2. Cover DIFFERENT interaction patterns and industries than the examples
3. Include a mix of: well-known platforms, niche services, B2B tools, mobile apps, and specialized tools
4. The description should emphasize: what entities users can manage, what operations are available, what workflows exist
5. Avoid content-heavy sites, real-time data sites, search engines, AI inference services
6. Each description should be 150-300 words, focusing on actionable features

Output format (JSON array, no markdown fences):
[
    {{"name": "Platform Name", "description": "Description focusing on CRUD operations, entities, and workflows..."}},
    ...
]

Generate exactly {num_to_generate} new scenarios:"""

SCENARIO_FOCUSED_GENERATION_USER_PROMPT_TEMPLATE = """Here are {num_examples} examples of suitable website/app scenarios:

{examples}

---

## IMPORTANT: Focus on generating scenarios in these specific categories:
{focus_categories}

Generate {num_to_generate} NEW and DIVERSE website/app/platform scenarios, with emphasis on the above categories.

Requirements:
1. Each scenario must be suitable for API environment synthesis (CRUD operations, database interactions)
2. Prioritize the focus categories above, but ensure variety within them
3. Include DIFFERENT sub-niches within each category (e.g., for E-commerce: fashion, electronics, B2B wholesale, auction, rental marketplace)
4. The description should emphasize: what entities users can manage, what operations are available, what workflows exist
5. Avoid content-heavy sites, real-time data sites, search engines, AI inference services
6. Each description should be 150-300 words, focusing on actionable features
7. Think of REAL platforms that exist, or realistic platforms that could exist

Output format (JSON array, no markdown fences):
[
    {{"name": "Platform Name", "description": "Description focusing on CRUD operations, entities, and workflows..."}},
    ...
]

Generate exactly {num_to_generate} new scenarios:"""


# 场景聚焦类别：用于引导 LLM 在特定领域内生成场景（每个子列表是一个大类及其细分）

SCENARIO_FOCUS_CATEGORIES = [
    ["E-commerce/Marketplace", "Retail", "Auction", "Rental marketplace"],
    ["Booking/Reservation", "Travel", "Hospitality", "Appointments"],
    ["Social/Community", "Forums", "Professional networks", "Dating"],
    ["Task/Project Management", "Collaboration", "Workflow automation"],
    ["Finance/Banking", "Investment", "Cryptocurrency", "Payments"],
    ["Subscription/Membership", "Loyalty programs", "Digital content"],
    ["Inventory/Catalog", "Warehouse management", "Asset tracking"],
    ["Messaging/Communication", "Customer support", "Ticketing systems"],
    ["Lists/Collections", "Bookmarks", "Curation", "Wishlists"],
    ["Scheduling/Calendar", "Event management", "Resource booking"],
    ["Forms/Surveys", "Feedback", "Applications", "Registrations"],
    ["Healthcare/Medical", "Telehealth", "Patient portals", "Pharmacy"],
    ["Education/Learning", "LMS", "Tutoring", "Course platforms"],
    ["Real Estate/Property", "Rentals", "Property management"],
    ["HR/Recruiting", "Job boards", "Applicant tracking", "Payroll"],
    ["Legal/Compliance", "Contract management", "Document signing"],
    ["Logistics/Shipping", "Fleet management", "Delivery tracking"],
    ["Food/Restaurant", "Ordering", "Menu management", "Kitchen operations"],
    ["Entertainment/Gaming", "Streaming", "Game platforms", "Virtual goods"],
    ["IoT/Smart devices", "Home automation", "Device management"],
    ["Fitness/Wellness", "Workout tracking", "Gym management", "Nutrition"],
    ["Pet services", "Veterinary", "Pet sitting", "Pet supplies"],
    ["Automotive", "Car dealership", "Service scheduling", "Parts inventory"],
    ["Non-profit/Charity", "Donations", "Volunteer management", "Fundraising"],
]


# —— 场景多样性检查提示词 ——
# 系统提示：告诉 LLM 它是"场景多样性检查器"，判断新场景是否与已有场景重复

SCENARIO_CLASSIFICATION_SYSTEM_PROMPT = """You are an expert at analyzing websites, apps, and digital platforms to determine their suitability for API environment synthesis and simulation.

## What is "API Environment Synthesis"?
We want to create SIMULATED API servers that mimic real platforms. An agent will interact with these APIs to complete user tasks. The simulated environment must:
- Have a SQLite database with synthesized (fake but realistic) data
- Provide RESTful API endpoints for CRUD operations
- Support user workflows with SYNTHESIZED data (not real external data)

## KEY PRINCIPLE: Can the data be SYNTHESIZED?

### Data that CAN be synthesized (fake but realistic):
- Numeric values with reasonable ranges: temperatures, prices, quantities, ratings, coordinates
- Structured entities: users, products, orders, bookings, employees, transactions
- Status/state values: order status, booking status, task progress
- Short text: names, titles, descriptions, addresses, categories
- Dates and times: timestamps, schedules, deadlines
- Geographic data: cities, countries, latitude/longitude (static, not real-time)
- Tabular data: flight schedules, stock historical data, weather records

### Data that CANNOT be synthesized meaningfully:
- Long-form content: news articles, blog posts, encyclopedia entries, research papers
- Media content: videos, audio, images (the actual content, not metadata)
- AI inference: chatbot responses, recommendations based on ML models
- Search results: ranked results from a real search index
- Real-time external feeds: live sports scores, breaking news, live stock tickers

## Suitability Criteria:

### HIGH Suitability (score 8-10):
Core functionality involves CRUD operations on SYNTHESIZABLE data. Examples:
- E-commerce: Amazon (products, cart, orders, reviews)
- Task Management: Trello, Jira (tasks, boards, assignments)
- Banking: Chase (accounts, transactions, transfers)
- Booking: OpenTable, Airbnb (reservations, listings)
- Weather Services: Weather apps (locations, forecasts, alerts - all synthesizable numeric data!)
- Flight Booking: Airlines, Kayak (flights, prices, schedules - all synthesizable!)
- Stock Trading: Robinhood (portfolio, trades, historical prices - synthesizable!)
- CRM: Salesforce (leads, contacts, deals)
- HR Systems: Workday (employees, time-off, payroll)
- IoT/Smart Home: Thermostats, sensors (device status, readings - synthesizable numbers!)

Key characteristics:
- Users CREATE, UPDATE, DELETE their own data
- Data is STRUCTURED and can be realistically faked
- Clear entity relationships
- Even "external data" like weather/stocks can be simulated with fake but plausible values

### MEDIUM Suitability (score 5-7):
Has CRUD features, but some core features require non-synthesizable content. Examples:
- YouTube: Playlist/subscription management (simulatable), but video content is the core value
- Spotify: Playlist/library management (simulatable), but audio is the core value
- Reddit/Forums: Posts, comments, votes (partially simulatable with short content)
- Social Media: Profile, follows, messaging (simulatable), but feed content quality varies

Key characteristics:
- CRUD operations exist and can be simulated
- Some features depend on content that's hard to synthesize meaningfully
- Can focus on the management/organizational aspects

### LOW Suitability (score 1-4):
Core value is NON-SYNTHESIZABLE content or requires real external services. Examples:
- Wikipedia: Core value is article CONTENT that cannot be faked meaningfully
- News sites (BBC, CNN): Article content IS the product
- Search Engines: Google, Bing (requires real search index and ranking)
- AI/LLM Services: ChatGPT (requires model inference)
- Translation Services: Google Translate (requires real translation models)
- Image Recognition: Needs real ML models

Key characteristics:
- The "content" or "intelligence" IS the product
- Cannot be replaced with fake data without losing all value
- Requires real ML models or massive real content databases"""


# —— 场景分类提示词 ——
# 用户提示：分析并分类某个网站/应用（给出类别、可模拟/不可模拟能力等）

SCENARIO_CLASSIFICATION_USER_PROMPT_TEMPLATE = """Analyze and classify the following website/app:

Name: {name}
Description: {description}

Provide a detailed classification:

1. **Categories**: What interaction pattern categories does this platform belong to? (select all that apply)
2. **Suitability Score**: 1-10 scale for API environment synthesis suitability
3. **Suitability Level**: "high", "medium", or "low"
4. **Reasoning**: Why is it suitable or not suitable?
5. **Simulatable Features**: What specific features CAN be simulated via CRUD APIs?
6. **Non-simulatable Features**: What features CANNOT be simulated?

Output format (JSON only, no markdown):
{{
    "categories": ["category1", "category2"],
    "suitability_score": 8,
    "suitability_level": "high",
    "reasoning": "Brief explanation",
    "simulatable_features": ["feature1", "feature2"],
    "non_simulatable_features": ["feature1", "feature2"]
}}"""
