# Solution Components Library

Ready-to-paste, proposal-grade write-ups for the Google Cloud services that appear in Devoteam G Cloud
technical proposals. Each section is written in the house voice established by the accepted Finnet Indonesia
proposal: formal, benefit-led, third person, product names spelled out on first use, acronyms expanded in
parentheses.

**How to use this file.** Pick only the components that actually appear in the proposed architecture — a
proposal that describes services the client is not buying reads as boilerplate and invites scope disputes.
Assemble the chosen sections in the order data flows through the architecture (ingest → store → transform →
govern → model → serve → consume), renumber the headings to match the document's `2.3.x` scheme, and paste
them into the `{{SOLUTION_COMPONENTS}}` token position.

**Accuracy rules.**
- Do not add SLA percentages, pricing, benchmark figures, or capability claims that are not already in this
  file. Every number here is either carried over from the accepted proposal or drawn from the public Google
  Cloud service-level agreements referenced in section 5.4.
- Anything marked `<!-- verify -->` is a claim or product name that Google has changed, rebranded, or may
  have superseded. Check it against current Google Cloud documentation before the document goes to a client.
- Where the client's own numbers are needed (volumes, row counts, latency targets), leave a
  `{{TBD — description}}` placeholder rather than estimating.
- Replace `the Customer` with `{{CLIENT_SHORT_NAME}}` when pasting, so the token-fill pass resolves it.

---

## BigQuery

BigQuery is Google Cloud's highly scalable, fully managed Lakehouse solution. It is serverless, provides
real-time insights from streaming data, has built-in machine learning (ML) out-of-the-box (OOTB), and a
high-speed in-memory BI Engine for faster reporting and analysis. BigQuery is designed to make data analysts
and data scientists more productive. It is a secure offering that provides data encryption by default, along
with further features that ensure data security and privacy.

**Upgrade and scale with power.** The Customer can increase its analytics capabilities with a serverless,
self-tuning, and highly scalable modern data warehouse that is easy to set up and manage, and that does not
require a database administrator. Teams can begin querying data — from gigabytes to petabytes — with standard
SQL in seconds. Data can be moved automatically from hundreds of popular business Software-as-a-Service (SaaS)
applications into BigQuery with the Data Transfer Service (DTS), or loaded with data integration tools to
Extract, Transform, Load (ETL) data at any scale from hybrid and multi-cloud applications. Federated queries
and the Storage API give access to multiple data sources, and public or commercial datasets can be joined
with the Customer's own data for richer insights. Flexible pricing models offer predictability through
capacity-based commitments alongside on-demand consumption.

**Accelerate time-to-value and lower total cost of ownership (TCO).** By removing infrastructure provisioning,
capacity planning, and cluster tuning from the operating model, BigQuery reduces the engineering effort
required to keep an analytics platform running. The budget and headcount released by that reduction can be
redirected towards digital innovation and IT initiatives that create business value.

**Get instant insights with real-time analytics.** BigQuery's high-performance streaming ingestion service
makes streaming data immediately available in the data warehouse for analysis, so the Customer can query data
on the fly and understand what is happening right now. Comprehensive batch and streaming data pipelines can be
built with Pub/Sub and Dataflow integrations, transforming data with equal reliability and expressiveness.

**Realize the power of predictive analytics.** ML can be operationalized, and business outcomes predicted,
without moving data out of the data warehouse. Models can be built using standard SQL with BigQuery ML, and
more advanced use cases can be unlocked through native Vertex AI integration, allowing models to be trained
on structured data in minutes. Geospatial analytics run natively through BigQuery's Geographic Information
System (GIS) functions to deliver location-based insights.

**Protect data, operate with trust, and share insights at scale.** BigQuery offers robust security,
governance, and reliability controls that deliver High Availability (HA) and a 99.99% Service Level Agreement
(SLA), giving the Customer peace of mind. Data is automatically replicated, restored, and backed up to ensure
business continuity. Sensitive data can be classified and redacted using Sensitive Data Protection <!-- verify: formerly Cloud Data Loss Prevention (DLP); confirm current product name -->,
and fine-grained access can be applied through Identity and Access Management (IAM). Data is encrypted at rest
and in transit by default, while Customer-Managed Encryption Keys (CMEK) provide direct control over key
material. Log data and events are monitored through native Cloud Logging and Cloud Monitoring integrations,
and BigQuery inherits the protections of Google's vertically integrated hardware and software security stack.

BigQuery is designed for independent scale, with compute and storage separated but connected through a
high-speed network to meet the needs of each query as it is submitted. Beyond multiple ingestion mechanisms,
it provides native ML capabilities through BigQuery ML, enabling users to create and execute ML models using
standard SQL queries, and federated access to external data sources.

A few additional features make BigQuery a powerful foundation for the Customer's use cases:

- BigQuery makes data widely available through its web-based SQL and PySpark workspaces and through
  high-performance APIs, with seamless integration into industry-standard Jupyter and Colab notebooks. A
  variety of tools can access data and run complex queries at scale via BigQuery's native APIs or via Open
  Database Connectivity (ODBC) and Java Database Connectivity (JDBC), including drivers compatible with
  Microsoft Excel.
- BigQuery Storage APIs allow massive parallel ingestion and export of datasets, offering a powerful
  integration point for other Google and third-party services.
- BigQuery can query terabytes of data in seconds, leveraging parallelism to process very high volumes of
  data.
- Idle slot sharing allows optional committed capacity to be shared across the organization, reducing waste
  of paid-for resources and improving the user experience. Borrowed slots are pre-empted within milliseconds
  if they are needed in the project to which they were originally assigned. Committed capacity models
  autoscale but can retain a configurable baseline of resources.
- BigQuery's search index capability makes it straightforward to run full-text search across an entire
  dataset, surfacing insights that traditional SQL queries might not reveal.
- BigQuery presents data lineage calculated from Dataplex, making it easy to see how data flows through the
  data warehouse — a valuable capability for troubleshooting and for assuring data accuracy.
- BigQuery allows isolation between operational workloads and analytical queries in both of its billing
  models. Under the on-demand billing model, separate projects can be dedicated to each type of workload and
  to each team running analytical queries. Under the autoscaling Editions billing models, specific
  reservations can be created for each workload type and team.
- BigQuery supports temporal partitioning and clustering in native tables, and Hive partitioning with
  external and BigLake tables, allowing efficient processing and resource savings. BigLake and external
  tables can be accelerated by metadata caching, which removes performance bottlenecks when data is spread
  across a large set of small files in a data lake.
- Native BigQuery tables do not require the maintenance tasks typical of Spark ecosystems, including location
  hints, file compaction, delta file merge operations, manual control of shuffle partitions, file size
  tuning, or management of column-level statistics.
- Analytics Hub offers a secure way to share data with partners and to integrate third-party datasets.

**When to include:** In essentially every data, analytics, or AI/ML proposal — BigQuery is the default
storage and query layer for Devoteam G Cloud data platforms.

### BigQuery Dataform

Dataform is a tool for managing data pipelines in BigQuery. It is a serverless platform that allows data
analysts and engineers to collaborate on SQL pipelines without writing procedural code or managing
infrastructure. Dataform uses a dependency-based workflow to ensure data pipelines are always up to date: when
a change is made to a table or view, Dataform automatically updates all downstream dependencies. Combined with
version control, this makes it straightforward to track changes to data pipelines and to ensure that every
team member is working from the same version of the transformation code.

Dataform also supports assertions — declarative data quality tests that run as part of the pipeline — so that
transformation logic and its validation live together in the same repository and are reviewed through the same
process.

**When to include:** Whenever the engagement builds SQL transformations inside BigQuery (raw → working →
golden zone modelling, ELT pipelines, curated marts). Omit if transformations are being delivered through
dbt, Dataflow, or a third-party ETL tool instead.

---

## Dataplex

While BigQuery and Cloud Storage serve as the infrastructure to store and process data, Dataplex provides a
single pane of glass to organize, govern, and index that data. <!-- verify: Google now markets this as "Dataplex Universal Catalog"; confirm the current product name before sending -->
Dataplex is a managed service that enables enterprises to group data containers from across their Google Cloud
deployment and organize them in a hierarchy — effectively overlaying a Lakehouse virtually on top of the
Google Cloud project hierarchy — with zones representing data domains within the Lakehouse. Data of any type
can be curated, cataloged, secured, integrated, and explored at any scale through an integrated experience.
Dataplex extends automatic data discovery and schema inference across different systems, so that once a
resource is added to Dataplex it is represented in the Lakehouse.

Dataplex automatically registers metadata as tables and filesets in the metastore and catalog. Together with
integrated sensitive-data inspection and built-in data quality checks, the tagging of sensitive data is
tightly integrated into the same workflow.

Dataplex enables a Data Mesh with simple security controls. Consistent security policy and enforcement across
Cloud Storage and BigQuery is available out of the box and allows central governance teams to audit the
environment. Managed data lake storage with fine-grained access control and BigQuery datasets are governed
through a single interface.

Data ownership and governance are key to the success of a Data Mesh, and require federated data sources with
integrated governance. Dataplex offers a data management and governance layer so that data across BigQuery
and Cloud Storage can be organized under centralized governance rules. Data administrators can set up and
manage workspaces together with appropriate environment profiles, including compute parameters and libraries,
while controlling user access and managing costs through one seamless interface.

Dataplex supports data governance across the entire platform, helping end users discover data with business
context, data quality, and data lineage information. Its intelligent data fabric enables organizations to
centrally discover, manage, monitor, and govern data across data lakes, data warehouses, and data marts with
consistent controls, providing access to trusted data and powering analytics at scale. Assets — whether in
BigQuery datasets, Cloud Storage object storage, or other storage systems — can be managed at scale, with data
domains defined within lakes and zones virtually layered over the physical location of the asset.

**When to include:** Any engagement with a governance, cataloguing, data quality, or regulated-industry
requirement. Mandatory for financial services proposals in Indonesia, where the client must evidence data
governance controls to the regulator.

### Data Catalog

Data Catalog is a Dataplex component that provides automatic data discovery, classification, and metadata
enrichment of structured, semi-structured, and unstructured data stored in Google Cloud and beyond, with
built-in data intelligence. <!-- verify: standalone Data Catalog has been folded into Dataplex Universal Catalog; confirm naming and any deprecation dates -->
Technical, operational, and business metadata can be managed for all data in a unified, flexible, and powerful
catalog. Users can search, find, and understand data through a built-in faceted-search interface that uses the
same search technology as Gmail.

**When to include:** Where the client needs a business glossary, searchable metadata, or a data marketplace
experience for analysts.

### Data Quality

Dataplex provides the following options to validate data quality:

- **Automatic data quality** provides an automated experience for obtaining quality insights about data. It
  automates and simplifies quality rule definition with recommendations and user-interface-driven workflows,
  standardizes insights through built-in reports, and drives action through alerting and troubleshooting.
- **Dataplex data quality tasks** offer a highly customizable experience for managing a bespoke rule
  repository and customizing execution and results, using Dataplex for managed, serverless execution. This
  option builds on an open-source component, CloudDQ, which also allows organizations to extend the engine to
  their own needs. <!-- verify: confirm CloudDQ and the data quality task remain the current recommendation -->

By ensuring the quality of data, the Customer can improve the accuracy of its analytics, make better
decisions, and build better products. Using Google Cloud data quality capabilities provides several
advantages:

- **Automated data quality checks:** data is scanned automatically for common quality issues, saving time and
  effort and helping identify problems early.
- **Customizable data quality checks:** data quality tasks give greater control over the data quality
  process; custom checks can be defined, or built-in checks used to get started quickly.
- **Open-source data quality engine:** the underlying engine can be used to define and execute custom data
  quality checks, giving the flexibility to tailor the data quality process to specific needs.

**When to include:** Where the proposal commits to data accuracy, reconciliation, or a "single trusted source"
outcome — which is most finance, risk, and regulatory reporting engagements.

### Data Lineage

Dataplex data lineage is a fully managed capability that helps the Customer understand how data is sourced and
transformed within the organization. It automatically tracks the movement of data across BigQuery and
integrated Google Cloud processing services, eliminating the operational burden of manual curation or
programmatic metering of lineage metadata. <!-- verify: the sample lists Cloud Data Fusion and Cloud Composer integrations as Preview; confirm current GA status before quoting specific integrations -->

Dataplex data lineage provides explainability for each relationship by detailing exactly what happened and
when, in an interactive lineage graph that delivers genuine data observability.

Data lineage provides a practical way to:

- Understand how data is sourced and transformed, with the help of lineage graph visualizations.
- Trace errors related to entries and data operations back to their root causes.
- Enable better change management through impact analysis — avoiding downtime or unexpected errors,
  understanding dependent entries, and collaborating with the relevant stakeholders.

**When to include:** Audit, compliance, and regulated-reporting engagements; also any platform where multiple
teams will build on shared curated tables and need impact analysis before changes.

---

## Cloud Composer

Cloud Composer is a fully managed workflow orchestration service built upon Apache Airflow. It provides the
following benefits:

- **Fully managed workflow orchestration.** Cloud Composer's managed nature and Apache Airflow compatibility
  allow teams to focus on authoring, scheduling, and monitoring workflows rather than on provisioning
  resources.
- **End-to-end integration with Google Cloud products** including BigQuery, Dataflow, Dataproc, Cloud Storage,
  Pub/Sub, and Vertex AI, giving users the freedom to fully orchestrate their pipelines.
- **Support for hybrid and multi-cloud estates.** Workflows can be authored, scheduled, and monitored through
  a single orchestration tool — whether the pipeline runs on premises, in multiple clouds, or entirely within
  Google Cloud.

Because pipeline definitions are expressed as Python code held in version control, orchestration logic is
reviewable, testable, and promotable across environments in the same way as application code.

**When to include:** Any engagement with scheduled batch pipelines, multi-step dependencies, or orchestration
across more than one processing service. For simple, single-service schedules, consider whether BigQuery
scheduled queries or Dataform release configurations are sufficient and cheaper — proposing Composer where it
is not needed inflates the run-rate estimate.

---

## Vertex AI

Vertex AI is Google Cloud's unified machine learning platform that helps teams build, deploy, and operate
models securely and at scale. It supports the full ML lifecycle — from data preparation and feature
engineering through model training, evaluation, and production monitoring — using managed infrastructure and
built-in Machine Learning Operations (MLOps) capabilities.

With Vertex AI, the Customer's teams can accelerate time-to-value for use cases such as forecasting, demand
planning, risk scoring, propensity modelling, and anomaly detection, while maintaining strong governance,
reliability, and performance in production.

Benefits of Vertex AI:

- **State of the art**
  - Powered by Google's ongoing research and innovation in the field of artificial intelligence (AI).
  - Provides a complete selection of AI models, including first-party (1P), open-source software (OSS), and
    third-party (3P) models.
- **End-to-end governance**
  - Prompting, tuning, and distillation allow users to customize AI models to their domain needs and use
    cases.
  - MLOps tooling provides comprehensive support for managing the AI model lifecycle, including evaluation,
    management, and deployment.
- **Enterprise readiness**
  - Data security: full control over the Customer's data during training and deployment.
  - Responsible AI: tools and support for building ethical and responsible generative AI.
  - Ready to use: developer-friendly tools that accelerate development time and increase efficiency.

Vertex AI is designed to ensure security, reliability, and scalability, helping AI builders focus on
innovation and on creating impactful solutions.

**When to include:** Every AI/ML engagement. For pure data-warehouse engagements, include only if a forecasting
or predictive component is genuinely in scope.

---

## BigQuery ML

BigQuery ML allows analysts and engineers to create, train, evaluate, and serve machine learning models using
standard SQL, directly inside BigQuery and without moving data to a separate training environment. Because the
model lives beside the data, the governance, access control, and audit posture already established for the
data warehouse extends automatically to the modelling workload.

The supported model families cover the majority of enterprise analytics use cases, including linear and
logistic regression, boosted trees, time-series forecasting, matrix factorization for recommendations, and
clustering for segmentation. Models can also be imported from, or exported to, Vertex AI, allowing a model
prototyped in SQL to graduate into a fully managed MLOps pipeline without being rewritten. BigQuery ML further
allows remote invocation of Vertex AI models — including Gemini models and text embedding models — from within
a SQL statement, so that generative AI enrichment and vector embedding generation can be expressed as part of
an ordinary transformation pipeline.

The commercial argument for BigQuery ML is speed of adoption. Analysts who already know SQL can deliver a
first predictive model in days rather than months, without a separate skills investment or a new platform to
operate. This makes it an effective first step for organizations building internal confidence in machine
learning before committing to a broader MLOps programme.

**When to include:** Where the client's analytics team is SQL-first, where a quick predictive win is required
alongside the data platform build, or where the use case is a well-understood tabular problem such as
forecasting, churn, or segmentation.

---

## Vertex AI Feature Store

Vertex AI Feature Store provides a managed, centralized repository for the features used to train and serve
machine learning models. It addresses two problems that consistently undermine production machine learning:
duplicated and inconsistent feature engineering across teams, and training-serving skew — the discrepancy
between the feature values a model saw during training and the values it receives in production.

Features are defined once, registered with their metadata and ownership, and then consumed by any authorized
team. Historical feature values can be retrieved point-in-time correct for training, while the same
definitions are served at low latency for online inference, so that the model receives consistently computed
inputs in both settings. Because features are catalogued and versioned, feature reuse becomes measurable and
lineage from source table to model input remains traceable.

Vertex AI Feature Store integrates directly with BigQuery as the source of feature data, which means the
Customer's existing curated tables become the feature substrate rather than requiring a parallel pipeline.

**When to include:** Engagements with multiple models in production, multiple teams building models on shared
data, or any real-time inference requirement where online and offline consistency must be demonstrated. Omit
for single-model proofs of concept, where it adds cost and complexity without a corresponding benefit.

---

## Vertex AI Pipelines

Vertex AI Pipelines provides serverless orchestration for machine learning workflows, allowing the full
training lifecycle to be expressed as a reproducible, versioned pipeline rather than as a sequence of manual
steps. Pipelines are authored using open standards and executed on managed infrastructure, so no orchestration
cluster needs to be provisioned or maintained.

A production pipeline typically encapsulates data validation, feature preparation, model training,
hyperparameter tuning, evaluation against a held-out dataset, and conditional registration of the resulting
model. Each run records its inputs, parameters, and artifacts, which gives the Customer a defensible audit
trail: for any model serving predictions in production, it is possible to identify the exact data, code, and
configuration that produced it.

Models that pass their evaluation gates are recorded in **Vertex AI Model Registry**, which maintains model
versions, their lineage, and their deployment state. Registered models are deployed to managed endpoints with
controlled traffic splitting, allowing a new version to be released to a fraction of traffic and promoted or
rolled back on evidence rather than on assumption.

**Vertex AI Model Monitoring** then observes the deployed model in production, detecting drift in the
distribution of incoming features relative to the training baseline, and skew between training and serving
data. Alerts on these signals let the Customer retrain deliberately, on a trigger, instead of discovering
model decay through a downstream business impact.

Together these capabilities constitute the MLOps backbone of the solution: reproducible training, governed
promotion, controlled deployment, and continuous monitoring.

**When to include:** Any engagement that puts a model into production and commits to maintaining it —
particularly where retraining cadence, model governance, or audit traceability is part of the requirement.
For a one-off analytical model with no production serving, this section is over-engineering.

---

## Vertex AI Model Garden and Gemini Models

Vertex AI Model Garden provides a single, governed catalogue of foundation models available to the Customer's
teams, spanning Google's first-party models, selected third-party models, and open-source models. Rather than
each team independently procuring and integrating a model provider, Model Garden makes model selection a
configuration decision inside an environment that already carries the organization's identity, networking,
logging, and data-residency controls.

Google's Gemini family of models provides natively multimodal reasoning across text, images, documents, audio,
and video, which is directly relevant where the Customer's source material is not clean structured text —
scanned documents, contact-centre recordings, photographs, or mixed-format reports. Gemini models are
available through Vertex AI with enterprise terms: prompts and responses submitted through Vertex AI are not
used to train Google's foundation models, and requests can be constrained to a chosen region to satisfy
data-residency requirements. <!-- verify: confirm current Vertex AI data-governance and regional-endpoint commitments against Google Cloud documentation before quoting -->

Models can be adapted to the Customer's domain through prompt engineering, grounding against the Customer's
own data, and supervised tuning, allowing accuracy to be improved incrementally without the cost and risk of
training a model from scratch. Because tuned models are managed as Vertex AI resources, they inherit the same
registry, deployment, and monitoring workflow as any other model in the platform.

**When to include:** Any generative AI engagement — summarization, extraction, classification, content
generation, conversational assistants, or code assistance. Also include where the client is evaluating
multiple model providers and needs a governed way to compare them.

---

## Vertex AI Agent Builder and Agent Engine

Vertex AI Agent Builder provides the tooling to design, build, and evaluate AI agents — applications in which a
model is given a goal, a set of tools, and access to the organization's data, and then plans and executes the
steps required to satisfy a user request. <!-- verify: Google has repeatedly restructured this portfolio (Agent Builder, Agent Development Kit, Agent Engine, AI Applications); confirm current product names and packaging -->
Agents are defined in terms of the instructions that govern their behaviour, the tools they may call — such as
a database query, an internal Application Programming Interface (API), or a document retrieval step — and the
guardrails that constrain them.

Agent Engine provides the managed runtime for those agents. It handles session and conversational state,
scaling, and integration with the surrounding Google Cloud security model, so that the Customer's team is
responsible for agent logic rather than for operating serving infrastructure. Agent behaviour is traceable:
each turn records the reasoning steps and tool invocations that produced the response, which is essential both
for debugging and for demonstrating to a risk function how an answer was reached.

For retrieval-augmented generation (RAG) use cases, agents ground their answers in the Customer's own
authoritative content, returning citations to the underlying source. This grounding is the mechanism by which
generative responses are constrained to organizational fact rather than to model recall, and it is the
principal control against fabricated answers in an enterprise deployment.

**When to include:** Conversational assistants, internal knowledge assistants, customer-service automation, and
any use case where the model must take actions or consult live systems rather than only generate text. Pair
with the Vertex AI Search section when the retrieval layer is a significant part of the scope.

---

## Vertex AI Search

Vertex AI Search provides enterprise-grade search and retrieval over the Customer's own content — documents,
websites, structured records, and unstructured repositories — delivered as a managed service rather than as a
search cluster to be built and tuned. <!-- verify: now positioned within "AI Applications"; confirm current product name -->

Content is ingested from sources such as Cloud Storage, BigQuery, and connected enterprise systems, then
indexed with both semantic and keyword retrieval so that a query matches on meaning as well as on exact terms.
Results respect the access controls associated with the underlying content, so a user retrieves only the
material they are already entitled to see — a prerequisite for deploying search across mixed-sensitivity
corpora.

As the retrieval layer of a retrieval-augmented generation architecture, Vertex AI Search supplies the grounded
context that a generative model reasons over, and returns citations alongside generated answers so that users
can verify the source. This removes the need for the Customer to build and operate a bespoke embedding
pipeline, vector index, and re-ranking stack, and substantially shortens the path from a document repository
to a working grounded assistant.

**When to include:** Enterprise knowledge search, document question-answering, support deflection, and as the
retrieval component of any RAG solution. If the Customer's retrieval corpus is small and already in BigQuery,
consider BigQuery vector search instead and say so.

---

## Document AI

Document AI converts documents into structured, queryable data. It combines optical character recognition
(OCR), layout understanding, and machine learning extraction to read documents in the form the business
actually receives them — scanned PDFs, photographs of paper, and mixed-quality digital files — and to return
labelled fields with confidence scores rather than an undifferentiated block of text.

The service provides general-purpose parsers for form and layout extraction, specialized parsers for common
document classes such as invoices, receipts, and identity documents, and the ability to train a custom
extractor on the Customer's own document types where the specialized parsers do not fit. <!-- verify: available specialized processors and their regional availability change; confirm the processors relevant to this deal are offered in the target region -->

Because each extracted field carries a confidence score, Document AI supports a human-in-the-loop operating
model: high-confidence extractions flow straight through to downstream systems, while low-confidence documents
are routed to a reviewer. This allows the Customer to automate the majority of volume immediately while
retaining control over the exceptions, and to raise the automation threshold over time as measured accuracy
improves.

Extracted output lands in BigQuery or Cloud Storage as structured records, at which point it is subject to the
same transformation, quality, and governance controls as any other dataset in the platform.

**When to include:** Any engagement involving document-heavy operational processes — claims, onboarding and
Know Your Customer (KYC) checks, accounts payable, contract review, or regulatory submissions. Especially
relevant where the client currently describes the work as "manual data entry".

---

## Looker and Looker Studio

Looker is Google Cloud's enterprise business intelligence platform. Its defining characteristic is the
semantic modelling layer, in which metric definitions, joins, and business logic are expressed once, in
version-controlled code, and then reused by every dashboard, report, and downstream consumer. This directly
addresses the failure mode in which different teams report different values for the same metric because each
has re-implemented the calculation in its own spreadsheet or dashboard.

Because the model resolves to queries executed in BigQuery rather than to an extract held inside the BI tool,
reporting operates against governed, current data, and the access controls applied in the warehouse continue to
apply at the point of consumption. Looker also supports delivery beyond the dashboard — scheduled reports,
alerting on metric thresholds, and embedding of governed analytics into the Customer's own applications and
operational workflows.

Looker Studio provides a lighter-weight, self-service option for ad hoc exploration and rapid dashboard
creation, and is well suited to teams that need to publish a small number of reports quickly without the
overhead of a modelled deployment. The two are frequently deployed together: Looker as the governed reporting
layer for metrics the business depends on, and Looker Studio for exploratory and departmental reporting.

**When to include:** Where the proposal includes a reporting or activation layer. If the Customer has an
incumbent BI tool — Tableau, Power BI, or similar — replace this section with a short statement that BigQuery
integrates with it through native connectors and standard ODBC/JDBC drivers, and do not propose a BI migration
that is not in scope.

---

## Dataflow

Dataflow is Google Cloud's fully managed service for stream and batch data processing, built on the open-source
Apache Beam programming model. A single pipeline definition can be executed in either streaming or batch mode,
which means the Customer maintains one body of transformation logic rather than parallel real-time and
historical implementations that inevitably diverge.

Dataflow is serverless: the service provisions, scales, and rebalances workers automatically in response to
the volume and skew of the incoming data, and releases them when the work is complete. There is no cluster to
size in advance and no capacity decision to revisit as volumes grow. For streaming pipelines, Beam's windowing
and watermark semantics provide correct handling of late and out-of-order events, so that aggregates over time
windows remain accurate under real-world delivery conditions.

Typical roles for Dataflow in the architectures Devoteam delivers include continuous ingestion from Pub/Sub
into BigQuery, enrichment and validation of records in flight, format conversion and complex transformation
that exceeds what is practical in SQL, and large-scale backfill of historical data.

**When to include:** Streaming ingestion, event processing, complex transformation beyond SQL, or large-scale
data migration. If all transformation is expressible in SQL over data already in BigQuery, propose Dataform
instead — it is simpler to operate and cheaper to run.

---

## Datastream

Datastream is a serverless change data capture (CDC) and replication service that streams changes from
operational databases — including Oracle, MySQL, PostgreSQL, and SQL Server — into Google Cloud destinations
such as BigQuery and Cloud Storage. <!-- verify: confirm the current source and destination matrix, and source-version support, for the databases in this client's estate -->

Datastream reads changes from the source database's transaction log rather than by repeatedly querying tables.
This is the decisive operational point in most proposals: replication imposes minimal additional load on the
production system, so analytics can be fed continuously without competing with the transactional workload that
the business depends on. It also removes the batch extraction window, which is frequently the constraint that
limits how fresh reporting can be.

The service handles the initial backfill of existing data and the transition to ongoing change streaming, and
propagates schema changes at the source so that pipelines do not silently break when a column is added.
Replicating directly into BigQuery produces continuously updated tables that reflect the current state of the
source system, providing a low-latency raw zone on which the transformation layer can build.

**When to include:** Any engagement whose sources are operational relational databases and where near-real-time
freshness matters, or where the client currently relies on nightly extracts that no longer complete within the
available window.

---

## Pub/Sub

Pub/Sub is Google Cloud's fully managed messaging service for event ingestion and delivery. It decouples the
systems that produce events from the systems that consume them: publishers write to a topic without knowing
which services will read it, and each consumer subscribes independently. New consumers can therefore be added
to an existing event stream without modifying the producing application — an important property when the
Customer's source systems are business-critical and change-controlled.

The service scales automatically to absorb variable and bursty load, buffering messages so that downstream
processing is protected from upstream spikes, and retains undelivered messages for a configurable period so
that a consumer outage results in delayed processing rather than data loss. Delivery guarantees, dead-letter
topics, and message ordering keys allow the Customer to define precisely how failure cases are handled.

In the architectures Devoteam delivers, Pub/Sub commonly serves as the ingestion boundary of the platform:
application and device events are published to a topic, then processed by Dataflow into BigQuery, or written
directly to BigQuery through a managed subscription for simpler cases.

**When to include:** Event-driven ingestion, application and clickstream telemetry, Internet of Things (IoT)
data collection, and as the buffer in front of any streaming pipeline. Omit for purely batch, database-sourced
architectures.

---

## Cloud Run

Cloud Run is a fully managed serverless platform for running containers. Any application that can be packaged
as a container and serve requests on a port can be deployed to Cloud Run, which handles provisioning, scaling —
including scaling to zero when there is no traffic — TLS termination, and revision management.

In AI/ML architectures, Cloud Run typically hosts the components that sit between the model and the business:
inference APIs that wrap a model with the Customer's pre- and post-processing logic, retrieval and
orchestration services for generative AI applications, internal web applications and demonstration interfaces,
and scheduled or event-triggered jobs. Because it is container-based, the same artifact runs locally, in test,
and in production, and the Customer is not constrained to a specific language or framework.

Cloud Run integrates with the platform's identity and networking controls: services can be restricted to
internal traffic, connected to a Virtual Private Cloud (VPC) to reach private resources, and authenticated
through IAM so that only authorized callers and service accounts can invoke them. Revisions support gradual
traffic migration, allowing a new version to be released to a portion of traffic and rolled back quickly if
required.

**When to include:** Wherever the solution includes a custom API, a user-facing application, an agent or RAG
backend, or a containerized batch job. For models served through a managed Vertex AI endpoint with no custom
logic, Cloud Run is unnecessary — do not include it to pad the architecture.

---

## Responsible AI and Model Armor

Google Cloud provides a set of controls that allow generative AI to be deployed with defined, auditable
safety boundaries rather than on trust in the model's default behaviour. This matters commercially as much as
technically: in regulated sectors, an AI system usually cannot be approved for production unless the
organization can describe what it will refuse to do and evidence that the control is enforced.

Vertex AI applies configurable safety filters to model requests and responses across established harm
categories, with thresholds that the Customer can tune to its own risk appetite. Grounding responses in the
Customer's authoritative content, and returning citations with each answer, constrains generated output to
verifiable organizational fact and gives reviewers a direct path from an answer to its source.

**Model Armor** provides an additional inspection layer applied to prompts and model responses, screening for
prompt-injection and jailbreak attempts, sensitive data disclosure, and malicious content, and applying the
Customer's policy consistently across applications rather than leaving each development team to implement its
own controls. <!-- verify: confirm Model Armor's current capability set, integration modes, and regional availability -->

These controls operate alongside the platform's existing governance: IAM for access to models and data, VPC
Service Controls for perimeter enforcement, CMEK for key control, and Cloud Logging for a durable record of
requests and responses. Devoteam's delivery approach documents the intended safety posture, the evaluation
performed against it, and the residual risks accepted by the Customer, so that the AI system enters production
with the same governance evidence expected of any other regulated workload.

**When to include:** Every generative AI proposal, and mandatory for financial services, healthcare, public
sector, and any engagement where a risk, compliance, or security function must approve the deployment.

---

## Google Cloud Infrastructure

Google Cloud data services such as BigQuery and Dataplex use serverless designs in which operators do not need
to manage server hardware, operating systems, associated management, or patching. Networking infrastructure
likewise requires no provisioning or configuration by the Customer.

BigQuery leverages shared infrastructure, and compute resources for analysis are on-demand by default. Once an
analytic query job completes, the resources scale to zero and no further compute charges are incurred. This is
unique among data warehousing solutions and makes BigQuery a cost-effective offering.

BigQuery computation — the engine that runs the queries — scales automatically to meet the requirements of the
job requested by analysts, in a fully managed fashion and without any need to manage or provision
infrastructure. By separating compute and storage resources, BigQuery provides independent vertical and
horizontal scaling.

Google Cloud operates a global infrastructure of data centres organized into zones and regions worldwide.
<!-- verify: the accepted sample quotes "121 zones across 40 regions"; this figure changes frequently — either refresh it from current Google Cloud documentation or leave it generic as written here -->
This means applications can be deployed close to users, and the Customer benefits from the high availability
and redundancy of Google's global network.

Because of its true serverless nature, BigQuery enables High Availability and Disaster Recovery (HA/DR) of the
environment in general — a materially different experience compared with node-based or virtual-machine-based
data warehouses. BigQuery offers an industry-leading 99.99% uptime SLA. This is made possible by BigQuery's
regional architecture, which writes data in two different zones and provisions redundant compute capacity. The
BigQuery SLA is the same for regions and for multi-regions.

Because BigQuery is a regional service, it is BigQuery's responsibility to handle the loss of a machine or even
an entire zone automatically. The fact that BigQuery is built on top of zones is abstracted from users. Machine
failures are an everyday occurrence at the scale at which Google operates, and BigQuery is designed to handle
them automatically without any impact on the operational state.

While short-lived zonal disruptions are not common, they do occur. BigQuery automation will fail queries over
to another zone within minutes of any severe disruption. Even if a zone were unavailable for a longer period,
no data loss would occur, because BigQuery writes data synchronously to two zones. In the face of zonal loss,
customers do not experience a service disruption.

BigQuery does not offer durability or availability in the extraordinarily unlikely and unprecedented event of
physical region loss. This is true for both region and multi-region configurations. Maintaining durability and
availability under such a scenario therefore requires customer planning.

To avoid data loss in the face of destructive regional loss, data must be backed up to another geographic
location. For example, the Customer could schedule a periodic export of a snapshot of its data to Cloud Storage
in a geographically distinct region. Alternatively, recurring jobs could replicate sensitive datasets to a
secondary BigQuery region.

**When to include:** In every proposal. This section carries the availability, resilience, and shared-
responsibility narrative, and it sets the expectation — before the SLA table in section 5.4 — that regional
disaster recovery is a customer planning decision rather than an implicit platform guarantee.
