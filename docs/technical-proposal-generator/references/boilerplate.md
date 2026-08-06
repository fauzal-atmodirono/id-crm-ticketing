# Devoteam Boilerplate Blocks

These are the reusable Devoteam prose blocks that recur, essentially unchanged, in every Devoteam G Cloud
technical proposal. **They already exist in the master template Google Doc** — the token-fill workflow does
not need to insert them, and in normal operation they should be left exactly as they are.

This file exists for three reasons:

1. **Review.** When a deal needs a variation (a different support model, a client that has negotiated
   different response times, a proposal with no managed-service component), this is the reference against
   which to check that the adaptation is deliberate rather than accidental.
2. **Rebuild.** If the master template is ever lost, corrupted, or superseded, these blocks are the source
   of record for reconstructing it.
3. **Token discipline.** Every place where the accepted source proposal named a specific person, email
   address, portal URL, or timezone has been replaced here with the exact `{{TOKEN}}` used in the master
   template. Use these spellings verbatim — the token-fill logic matches on them literally.

**Tokens that appear in these blocks:**
`{{CLIENT_LEGAL_NAME}}`, `{{CLIENT_SHORT_NAME}}`, `{{SUPPORT_EMAIL}}`, `{{SUPPORT_PORTAL_URL}}`,
`{{SUPPORT_TIMEZONE}}`, `{{SDM_NAME}}`, `{{SDM_EMAIL}}`, `{{TECH_LEAD_NAME}}`, `{{TECH_LEAD_EMAIL}}`,
`{{TAM_NAME}}`, `{{TAM_EMAIL}}`, `{{ESCALATION_L3_NAME_1}}`, `{{ESCALATION_L3_EMAIL_1}}`,
`{{ESCALATION_L3_NAME_2}}`, `{{ESCALATION_L3_EMAIL_2}}`, `{{INDUSTRY_CREDENTIAL}}`,
`{{MANAGED_SERVICE_INTRO}}`.

**Do not change without approval:** the SLA tables, the response-time commitments, and the Service Level
Infrastructure Uptime disclaimer are contractual language. Altering a number in these blocks changes what
Devoteam is committing to deliver. If a deal requires different terms, escalate rather than edit.

---

## Block 1 — Company Profile (section 1)

> Devoteam is a leading consulting firm focused on digital strategy, tech platforms, cybersecurity and
> business transformation. Devoteam G Cloud is Devoteam's Google pillar, and has more than 400+ talents
> globally, with over 600+ certifications. We are a premier managed services partner and authorized training
> partner, and we handle all end to end from enablement, solution design, implementation, and project
> operations. {{INDUSTRY_CREDENTIAL}}
>
> Here are some reasons why you should collaborate with Devoteam:
>
> - **Expertise and Experience:**
>   - **Deep Google Cloud Expertise:** Devoteam has over 600 Google Cloud experts and 13 years of experience
>     working with the Google Cloud platform. They have a deep understanding of the Google Cloud ecosystem and
>     can help you navigate the complexities of cloud migration, implementation, and management.
>   - **Global Reach:** Devoteam has a global presence with offices in 18 countries, allowing them to provide
>     local support and expertise to clients around the world.
>   - **Industry-Specific Expertise:** Devoteam has experience working with a wide range of industries,
>     including Telco, Financial Services, and Retail. They understand the unique challenges and opportunities
>     faced by different industries and can tailor their solutions accordingly.
> - **Comprehensive Services:**
>   - **End-to-End Cloud Solutions:** Devoteam offers a comprehensive suite of services, including cloud
>     strategy, cloud migration, cloud optimization, cloud security, and cloud managed services. They can help
>     you with every aspect of your cloud journey, from planning to implementation and ongoing support.
>   - **Managed Services:** Devoteam offers managed services that can help you free up your internal resources
>     and focus on your core business. They can manage your Google Cloud environment, ensuring that it is
>     always secure, reliable, and performing at its best.
>   - **Innovation and Agility:** Devoteam is committed to innovation and uses the latest technologies and
>     methodologies to deliver value to its clients. They are constantly researching and developing new
>     solutions and services to help their clients stay ahead of the curve.
> - **Strong Partner Ecosystem:**
>   - **Google Cloud Partner of the Year:** Devoteam has been recognized as a 5x Google Cloud Partner of the
>     Year, demonstrating their commitment to customer success and their expertise in Google Cloud.
>   - **Strong Partnerships:** Devoteam has a strong network of partners, one of them is Google Cloud. This
>     allows them to provide a wide range of solutions and services to their clients.
> - **Value for Money:**
>   - **Competitive Pricing:** Devoteam offers competitive pricing and flexible payment options to meet the
>     needs of its clients.
>   - **Value-Driven Approach:** Devoteam is committed to delivering value to its clients. They focus on
>     helping clients achieve their business goals and providing a return on investment.
> - **Cybersecurity Expertise:**
>   - **Cybersecurity Consulting and Audit:** Devoteam has a strong emphasis on cybersecurity and offers
>     services such as penetration testing, application development, and security audits. They can help you
>     protect your organization from cyber threats and ensure the security of your data and applications.

**Adaptation notes.**

- The **first paragraph** is the only part that is deal-sensitive, and its last sentence is the
  `{{INDUSTRY_CREDENTIAL}}` token — the master template carries the token here, not prose. Fill it with one
  sentence naming *this* deal's industry and its regulator. The accepted source proposal's fill, which is the
  right one for banks, insurers, payments companies, multifinance and fintech, was:

  > We have also worked with other financial institutions clients in Indonesia and are familiar with OJK
  > regulations locally.

  For a client in another sector, write the equivalent sector claim — but only one Devoteam can actually
  evidence, and never cite a regulation number you have not verified. An unsupportable credential in the
  opening paragraph is the fastest way to lose the room. Never leave the financial-services sentence in place
  for a non-financial-services client.
- The **counts** (400+ talents, 600+ certifications, 600 Google Cloud experts, 13 years, 18 countries, 5x
  Partner of the Year) are corporate marketing figures. They are reproduced here as they appear in the
  accepted proposal. Refresh them from current Devoteam collateral if the proposal is going to a client who
  will diligence them, and note that "400+ talents" and "over 600 Google Cloud experts" are internally
  inconsistent in the source — pick one framing.
- The block is written in mixed second and third person ("you should collaborate with Devoteam" / "They have
  a deep understanding"). This is how it appears in the accepted document and it has not been an objection.
  Do not partially fix it; either leave it or normalize the whole block in one pass.

---

## Block 2 — Agile Methodology (section 5.1)

> The team executes in an agile way, using weekly or bi-weekly sprints with demos at the end of each sprint,
> but still having milestones.
>
> Starting with a thorough assessment and preparation stage, the team then dives into brainstorming,
> generating ideas and defining the core functionalities of the product. The subsequent phases focus on
> conceptual design and technical architecture, ensuring the product's viability and adherence to user needs.
>
> Finally, the product is built and deployed, marking the successful completion of the Agile sprint. This
> iterative approach, using sprints of one to two weeks, allows for continuous improvement and adaptability to
> changing requirements, while still maintaining clear milestones for progress tracking. This methodology
> promotes a collaborative and flexible development environment, ensuring that the final product meets the
> ever-evolving demands of the users.

**Adaptation notes.** Sprint length is stated twice ("weekly or bi-weekly", "one to two weeks"). If the deal
commits to a specific cadence, set both consistently. Do not remove the reference to milestones — for
Indonesian enterprise and state-owned clients, milestone-based progress tracking is usually what the
procurement and payment schedule is anchored to.

---

## Block 3 — Change Management and Project Communication (section 5.2)

> All change requests that arise within this project will be assessed by all parties, Change Request Documents
> will be created by Devoteam and approved and informed by {{CLIENT_LEGAL_NAME}} before entered in the
> development and implementation state by Devoteam. Document Approval will be Project Manager in Devoteam side
> and Authorized Person by {{CLIENT_SHORT_NAME}}.
>
> Proper communication channels are important to discuss, decide, notify and escalate issues. As we have agile
> methodology, we conduct sprint planning at the beginning of each sprint. If required, we conduct daily
> stand-up.

**Adaptation notes.** This block is the commercial control on scope creep, and it is the paragraph most worth
protecting during negotiation. It establishes that (a) changes are documented before work starts, and (b)
approval is a named-role decision on both sides. If a client asks to soften it, treat that as a scoping risk
and flag it rather than accepting the edit silently.

---

## Block 4 — Communication Channels (section 5.3)

> - **Email:** Primary and formal channel for notification, request and escalation.
> - **Sprint planning:** backlogs grooming, assignment and update/review at the beginning of each sprint.
> - **Sprint review:** MVP demo and update/review at the end of each sprint.
> - **Final presentation:** Final of the project.

**Adaptation notes.** Add a chat channel (Google Chat, Slack, WhatsApp group) only if the delivery team has
agreed to monitor it. Never list a channel as a formal escalation route unless it is covered by the support
SLA in section 6.2 — email and the support portal are the only channels the SLA is written against.

---

## Block 5 — Google Cloud Platform Service Level Agreement (section 5.4)

> Google Cloud Platform (GCP) has a strong commitment to providing reliable and high-performance services to
> its customers. To ensure that our products meet the demanding needs of businesses, we have implemented
> rigorous Service Level Agreements (SLAs) that guarantee specific levels of availability, performance, and
> operational support. These SLAs provide customers with confidence in the reliability and stability of GCP's
> infrastructure, allowing them to focus on their core business objectives without worrying about disruptions
> or downtime.

| Product | Monthly Uptime Percentage | Reference |
|---|---|---|
| BigQuery | >= 99.99% | https://cloud.google.com/bigquery/sla |
| Cloud Storage (Standard) | >= 99.9% | https://cloud.google.com/storage/sla |
| Cloud Composer | >= 99.5% | https://cloud.google.com/composer/sla |
| Dataplex | >= 99.5% | https://cloud.google.com/dataplex/sla |

**Adaptation notes.**

- **The table must list the services actually proposed.** If the architecture includes Dataflow, Pub/Sub,
  Cloud Run, or Vertex AI, add a row for each; if Cloud Composer is not in scope, remove its row. Leaving a
  service in the table that is not in the architecture is a commitment to something not being delivered.
- **Never type an SLA percentage from memory.** Open the service's SLA page, read the current Monthly Uptime
  Percentage, and cite that page in the Reference column. SLA terms are versioned and do change.
- Some services have tiered SLAs that depend on configuration (for example, zonal versus regional
  deployment). Where that is the case, state the configuration the figure applies to.

---

## Block 6 — Google Cloud Platform Enhanced Support (section 6.1)

> Google Enhanced Support offers unlimited technical support for outages and defects, unexpected product
> behavior, product usage questions and billing issues.
>
> When managing support cases as a Premium Support customer, you have access to the following features:
>
> - **P1 response SLO:** For Priority 1 (P1) support cases, receive the first meaningful response within
>   1 hour.
> - **24/7 availability:** Receive support 24 hours a day, 7 days a week (24/7) for cases of certain priority
>   and language.
> - **Language support:** Request support across multiple languages, including English, Japanese, Mandarin
>   Chinese, Korean, and French. For details on language support for Customer Care, visit Language Support and
>   Working Hours.
> - **Case escalation:** Escalate to request additional attention for ongoing support cases.

| Priority | Target Initial Response Times |
|---|---|
| P1 | 1 hour |
| P2 | 4 hours |
| P3 | 8 hours* |
| P4 | 8 hours* |

> \* during the Hours of Operation

**Adaptation notes.** The source text switches between "Enhanced Support" in the heading and "Premium Support"
in the body. Confirm which Google Cloud Customer Care tier the client is actually purchasing and make the
block consistent — the tiers carry different response commitments and different prices, and quoting the wrong
one is a commercial exposure, not a typo.

---

## Block 7 — Devoteam Support and Managed Services (section 6.2)

### 7a. Opening statement

> Devoteam Enhanced Support offers unlimited technical support for outages and defects, unexpected product
> behavior, product usage questions and billing issues.

### 7b. Incident Management RACI

> **Incident Management**

| Incident Management | Devoteam | Customer |
|---|---|---|
| Perform 24x7 incident management support | R, A | C, I |
| Generate incident tickets based on events | R, A, C, I | R, C, I |
| Evaluate and categorize incidents for prioritization | R, A, C | I |
| Respond to and remediate incidents within agreed SLO / KPI | R, A, C | I |
| Manage incident escalation to Google via Partner-led Premium Support | R, A, C | I |
| Support and troubleshoot incidents for tier 1 end-users | I | R, A, C |
| Manage and/or resolve incidents outside the scope of the managed assets on cloud platform | I | R, A, C |

R = Responsible, A = Accountable, C = Consulted, I = Informed. The last two rows are the important ones
commercially: tier-1 end-user support and anything outside the managed assets remain the Customer's
responsibility.

### 7c. Devoteam Support and Managed Service SLA

| Priority level | Devoteam Support* | Devoteam Managed Services Incident — Begin Work | Devoteam Managed Services Incident — Resolve |
|---|---|---|---|
| P1 Critical Impact | 30 minutes 24x7 | 15 minutes 8x5 *** | 3 hours 8x5 *** |
| P2 High Impact | 2 hours 24x7 | 1 hours 24x7 | 8 hours 24x7 |
| P3 Medium Impact | 6 hours 8x5 ** | 2 hours 8x5 ** | 24 hours 8x5 ** |
| P4 Low Impact | 10 hours 8x5 ** | 16 hours 8x5 ** | 40 hours 8x5 ** |

> \* Oncall Support
>
> \*\* Oncall Support for 8x5 from 9:00 AM to 5:00 PM {{SUPPORT_TIMEZONE}}, Monday to Friday. Weekend and
> national holidays are excluded.
>
> \*\*\* Onsite Managed Services for 8x5 from 9:00 AM to 5:00 PM {{SUPPORT_TIMEZONE}}, Monday to Friday.
> Weekend and national holidays are excluded.

**Adaptation notes.** `{{SUPPORT_TIMEZONE}}` fills with the spelled-out form, for example
`WIB (Western Indonesia Time)` or `WIT (Eastern Indonesia Time)`. Note that the accepted source proposal
uses `WIT (Western Indonesia Time)`, which is incorrect: in Indonesian usage WIB is Waktu Indonesia Barat
(Western, UTC+7), WITA is Waktu Indonesia Tengah (Central, UTC+8), and WIT is Waktu Indonesia Timur (Eastern,
UTC+9). Devoteam Indonesia's delivery hours are Jakarta time, so **`WIB (Western Indonesia Time)` is almost
always the correct fill.** Confirm against the client's operating location before finalizing.

### 7d. Devoteam Support Contact Channel

> **Devoteam Support Contact Channel**
>
> Devoteam provide customer with support contact channel such as,
>
> - **Support Portal**
>
>   New cases can be opened by GCP customers via the link:
>
>   {{SUPPORT_PORTAL_URL}}
>
> - **Email**
>
>   You can contact our services by sending an email to:
>
>   {{SUPPORT_EMAIL}}

### 7e. Service Level Infrastructure Uptime

> **Service Level Infrastructure Uptime**
>
> Devoteam will pass through any applicable service level agreement pertaining to any Google product or
> services purchased by the Customer. Devoteam does not provide any uptime or availability guarantees besides
> those defined by Google.

**Adaptation notes.** This disclaimer is the boundary between Devoteam's response-time commitments (block 7c)
and Google's platform uptime commitments (block 5). It must not be removed, and it must not be softened. It is
what prevents the Devoteam SLA table from being read as an availability guarantee for the Google Cloud
services themselves.

### 7f. Escalation Flow

> **Escalation Flow**

| Flow | PIC Name |
|---|---|
| Level 1 | Support Email ( {{SUPPORT_EMAIL}} ) |
| Level 2 | {{SDM_NAME}} ( {{SDM_EMAIL}} ) |
| Level 3 | {{ESCALATION_L3_NAME_1}} ( {{ESCALATION_L3_EMAIL_1}} ) {{ESCALATION_L3_NAME_2}} ( {{ESCALATION_L3_EMAIL_2}} ) |

> Notes : The name of personnel in the table can be adjusted based on Devoteam policy, and Devoteam will be
> informed to the customer at least 10 working days prior.

**Adaptation notes.** The Level 3 cell holds two people in a single cell. `{{SDM_NAME}}` appears both here as
Level 2 and in the Roles table (block 7h) as Service Delivery Manager — that is intentional and correct; the
same person holds both positions. PIC = Person In Charge, a standard term in Indonesian business documents;
leave it as-is.

### 7g. Hierarchical Escalation

> **Hierarchical Escalation**
>
> The hierarchical escalation of the incident should be performed whenever any of the following situations
> occur:
>
> - the agreed time for the recovery of the service should be approached without any prospect of such
>   occurrence;
> - there are obstacles to the recovery of the service within the agreed time, created by the end customer,
>   other support teams or any other unforeseen factor;
> - there is a widespread and high impact on the service or business of the end customer;
> - a VIP user is affected (eg a CEO of one of the operators);
> - there is a hierarchical escalation of the incident in the structure of the final customer;
>
> Hierarchical escalation according to priority and SLA evolution is defined in the matrix below. Escalation
> is done manually via phone or by email.

| | | SLA Consumption | | |
|---|---|---|---|---|
| | | **50%** | **75%** | **100%** |
| **Priority** | P1 Critical | Technical Lead | Technical Lead<br>Service Delivery Manager | Technical Lead<br>Service Delivery Manager<br>Technical Account Manager |
| | P2 High | Technical Lead | Technical Lead<br>Service Delivery Manager | Technical Lead<br>Service Delivery Manager<br>Technical Account Manager |
| | P3 Medium | | Technical Lead<br>Service Delivery Manager | Technical Lead<br>Service Delivery Manager<br>Technical Account Manager |
| | P4 Low | | Technical Lead | Technical Lead<br>Service Delivery Manager |

**Adaptation notes.** The fourth bullet says "eg a CEO of one of the operators" — a leftover from a telco
engagement. For a non-telco client, change it to "eg a member of the Customer's executive leadership". This
is one of the few genuine defects in the source boilerplate and is worth fixing every time.

### 7h. Roles

| Roles Name | PIC Name |
|---|---|
| Technical Lead | {{TECH_LEAD_NAME}} ( {{TECH_LEAD_EMAIL}} ) |
| Service Delivery Manager | {{SDM_NAME}} ( {{SDM_EMAIL}} ) |
| Technical Account Manager | {{TAM_NAME}} ( {{TAM_EMAIL}} ) |

> Notes : The name of personnel in the table can be adjusted based on Devoteam policy, and Devoteam will be
> informed to the customer at least 10 working days prior.

**Adaptation notes.** The "Notes :" disclaimer appears twice in section 6.2 — once after the Escalation Flow
table and once after the Roles table. Both instances are intentional; keep both. The disclaimer is what allows
Devoteam to change assigned personnel without issuing a contract amendment, so it must survive any editing
pass. Note the spacing (`Notes :` with a space before the colon) matches the source document.

---

## Block 8 — Scope of Works for Maintenance and Managed Service (section 6.3)

The **intro sentence** of this section is client-specific and is held in the template as
`{{MANAGED_SERVICE_INTRO}}`. In the accepted proposal it reads, in substance: *the scope includes both
proactive preventive actions and reactive corrective measures, ensuring [the client] receives ongoing support,
minimizes downtime, and optimizes the effectiveness of their [solution domain]*. Rewrite it to name the actual
solution being maintained, then follow it with the bullets below, which are reusable verbatim.

> - **Preventive Maintenance:**
>   - **System Updates and Security Patches:** Regularly applying software updates and security patches to
>     ensure system stability, performance, and compliance.
>   - **System Optimization:** Optimizing system configuration, tuning databases, and allocating resources
>     effectively for optimal performance.
>   - **Data Integrity Checks:** Regularly checking the integrity of the data to ensure accuracy and prevent
>     data corruption.
>   - **Security Audits:** Performing routine security audits to ensure compliance with security standards and
>     identify potential vulnerabilities.
> - **Corrective Maintenance:**
>   - **Prompt Response to Issues:** Quickly addressing system issues, bugs, or hardware failures to minimize
>     downtime and impact on operations.
>   - **Data Recovery:** Implementing recovery procedures from backups to restore data integrity and system
>     functionality in case of failures.
>   - **Root Cause Analysis:** Investigating issues to identify the underlying causes and implement corrective
>     measures to prevent recurrence.
> - **Managed Services:**
>   - **On-Demand Support:** Providing support and assistance to {{CLIENT_SHORT_NAME}}'s IT team on-demand,
>     including troubleshooting issues, addressing questions, and resolving problems.
>   - **8x5 Onsite Support:** Offering on-site support services, including troubleshooting complex issues and
>     providing expert assistance during system installations or upgrades.
>   - **24x7 On-call Support:** Providing round-the-clock support services, enabling
>     {{CLIENT_SHORT_NAME}} to receive help even during off-hours for urgent or critical situations.
>   - **System Monitoring:** Continuously monitoring the system for performance, stability, and security to
>     detect and address issues proactively.

**Adaptation notes.**

- Section 6 is titled "Post Implementation (Optional)". If managed services are not being sold, the whole
  section can be removed — but say so explicitly to the client rather than deleting it silently, because the
  Devoteam SLA table in block 7c is the client's main comfort on post-go-live support.
- "8x5 Onsite Support" commits Devoteam personnel to the client's premises. Confirm this is genuinely in the
  commercial scope and priced before leaving the bullet in. For a Jakarta client this is routine; for a client
  outside Jabodetabek it carries travel cost that must be reflected in the price.
- The maintenance bullets are written for a data-platform engagement. For an AI/ML engagement, consider adding
  model-specific preventive activities — monitoring for prediction drift and training-serving skew, scheduled
  retraining and re-evaluation, and periodic review of safety-filter and grounding configuration — so the
  managed-service scope actually matches what was built.
