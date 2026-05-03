---
# Fixture metadata (stripped by loader)
scenario: "Contradictory expert opinions on architecture — project context strongly favors one side"
notes:
  - "Strong arguments on both sides of microservices vs monolith"
  - "Project context (5-person team, simple CRUD) clearly favors monolith"
  - "Goal: verify skill does NOT fabricate consensus, DOES weigh evidence against context"
  - "Goal: verify skill makes a recommendation despite disagreement, not 'it depends'"
---

## Research Question

Should we use microservices or a monolith for our new inventory management service?

## Project Context

- **Team size:** 5 developers (1 senior, 2 mid, 2 junior)
- **Application type:** Internal inventory management CRUD app
- **Expected load:** ~500 concurrent users, ~2,000 requests/minute peak
- **Data model:** Products, warehouses, stock levels, transfers, audit log
- **Integration points:** Existing ERP system (REST API), email notifications
- **Infrastructure:** Single Kubernetes cluster, team has basic K8s experience
- **Timeline:** MVP in 3 months, full feature set in 6 months
- **Team experience:** 3 developers have monolith experience, 1 has microservices experience, 1 is new to backend

## Simulated Search Results

### Source 1: "Microservices at Scale — Lessons from Netflix" (InfoQ, 2025)

Netflix engineering describes how microservices enabled independent deployments, team autonomy, and fault isolation. Key stats: 1,000+ microservices, 200+ engineering teams, millions of requests per second. Their service mesh handles inter-service communication, distributed tracing, and circuit breaking. "Microservices were essential to our ability to scale both our technology and our organization."

### Source 2: "We Moved to Microservices and Regretted It" (blog post, 2025-07)

A 12-person startup describes splitting a monolith into 8 microservices. Results after 1 year:
- Deployment time increased from 5 minutes to 45 minutes (coordinating multiple services)
- Debugging time tripled due to distributed tracing complexity
- 2 developers spent 40% of their time on infrastructure instead of features
- Data consistency bugs appeared that didn't exist in the monolith
- They migrated back to a modular monolith after 14 months

Key quote: "We had Netflix's architecture but not Netflix's problems. Or their 2,000-engineer platform team."

### Source 3: "Monolith First" — Martin Fowler (martinfowler.com, 2025 update)

Fowler reaffirms his original 2015 advice: "Almost all successful microservice stories started with a monolith that got too big and was broken up." Updated reasoning:
- You don't know your domain boundaries well enough at the start
- Microservice boundaries chosen wrong are 10x more expensive to fix than monolith module boundaries
- "The only justified reason to start with microservices is when you KNOW the domain very well AND have the team to support distributed systems."

### Source 4: "Why Microservices Are the Future" — ThoughtWorks Technology Radar (2025)

ThoughtWorks moved microservices from "Trial" to "Adopt" in 2025. Key arguments:
- Cloud-native tooling (Kubernetes, Istio, Dapr) has reduced operational overhead significantly
- Event-driven architectures with microservices enable better data consistency patterns
- Organizations that delay microservices adoption face increasing migration costs
- "For any greenfield project expected to grow, microservices provide architectural flexibility that's hard to retrofit."

### Source 5: "The Majestic Monolith" — DHH (Signal v Noise, 2025 update)

Basecamp's CTO argues monoliths remain the right choice for most teams: "We serve millions of users with a monolith and a team of 15 programmers. The complexity tax of microservices would slow us to a crawl." Points out that Shopify (one of the largest Rails apps) is still a modular monolith.

### Source 6: "Right-Sizing Services — When to Split" (Sam Newman, 2025)

Author of "Building Microservices" notes that team size is the strongest predictor of microservices success. His heuristic: "If your entire backend team fits in one meeting room, you probably don't need microservices. The coordination overhead exceeds the benefits." He distinguishes between organizational scaling (microservices help) and technical scaling (horizontal scaling of a monolith often suffices).

### Source 7: Google SRE — "Distributed Systems Complexity Budget" (2025)

Google's SRE team introduced the concept of "complexity budget" — the total distributed systems complexity a team can manage scales with team size and experience. Their model:
- Team of 5: complexity budget supports 1-3 services
- Team of 15: complexity budget supports 5-10 services
- Team of 50+: complexity budget supports 20+ services

"Exceeding your complexity budget leads to incidents caused by the architecture itself, not by business logic."

### Source 8: "Modular Monolith — The Best of Both Worlds" (Conference talk, PyCon 2025)

Speaker advocates for modular monoliths with clear module boundaries, separate databases per module (within the same deployment), and explicit APIs between modules. Claims this provides 80% of microservices benefits (independent development, clear ownership) with 20% of the operational cost. Migration to true microservices later is straightforward if needed.

### Source 9: "Microservices for Small Teams — A Case Study" (Medium, 2025-04)

A 6-person team describes successfully running 4 microservices for a fintech product. They attribute success to:
- Using a service mesh that handled all infrastructure concerns
- Strong DevOps culture from day one
- Very well-defined domain boundaries (payments, accounts, notifications, reporting)
- 2 of 6 engineers focused exclusively on platform/infrastructure

Caveat from comments: "You basically had a 4-person feature team and a 2-person platform team. Most 6-person teams can't afford that ratio."

### Source 10: Stack Overflow Developer Survey 2025 — Architecture Section

- 58% of respondents working on monoliths report satisfaction with their architecture
- 47% of respondents working on microservices report satisfaction
- Most-cited microservices pain point: "debugging across service boundaries" (72%)
- Most-cited monolith pain point: "deployment coupling" (61%)
- Teams under 10 developers report higher satisfaction with monoliths (68% vs 34% for microservices)
