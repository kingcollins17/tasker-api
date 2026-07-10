# Task and Bidding Architecture Implementation

This document describes the concrete database schemas, components, and endpoints implemented for the Tasker marketplace.

---

## 1. Database Schema Definitions

All entities are built using SQLModel and SQLAlchemy mapped types.

### `tasks`
Contains core information describing the work requested by the customer.
- `id` (str, primary key): UUID4.
- `customer_id` (str, foreign key -> `users.id`): Seeker who posted the task.
- `region_id` (str, foreign key -> `regions.id`, index): User's active region ID at post time, populated automatically.
- `title` (str, required): Summary of work.
- `description` (str, required): In-depth details.
- `category_id` (str, foreign key -> `categories.id`, nullable): High-level category.
- `service_id` (str, foreign key -> `services.id`, nullable): Specific service type.
- `budget_min` (float, nullable)
- `budget_max` (float, nullable)
- `pricing_model` (str, default "fixed"): Fixed price or hourly model.
- `status` (TaskStatus, enum): Current task status.
- `visibility` (str, default "public")
- `expires_at` (datetime, nullable)
- `created_at` (datetime, indexed)
- `updated_at` (datetime)

**Task Statuses (`TaskStatus` Enum):**
- `draft`: Task is being edited.
- `open`: Open for bids.
- `matching`: Matching algorithms run in background.
- `bidding`: Active bidding stage (has received at least one bid).
- `assigned`: Bid accepted; provider assigned.
- `in_progress`: Task execution started.
- `completed`: Task successfully completed.
- `cancelled`: Task cancelled.
- `expired`: Task reached deadline before matching.

---

### `task_locations`
Stored separately from the core task table for normalized queries and spatial optimization.
- `id` (str, primary key): UUID4.
- `task_id` (str, foreign key -> `tasks.id`, unique, index)
- `latitude` (float)
- `longitude` (float)
- `address` (str, nullable)
- `city` (str, nullable)
- `state` (str, nullable)
- `country` (str, nullable)
- `geography_point` (PointType, geometry/POINT, SRID 4326): PostGIS representation.

---

### `task_bids`
Stores bids/offers made by provider profiles on open tasks.
- `id` (str, primary key): UUID4.
- `task_id` (str, foreign key -> `tasks.id`, index)
- `provider_id` (str, foreign key -> `users.id`, index)
- `price` (float)
- `message` (str, nullable)
- `estimated_duration` (str, nullable): Estimate text (e.g., "3 hours").
- `status` (TaskBidStatus, enum, default `pending`)
- `created_at` (datetime, indexed)
- `updated_at` (datetime)

**Bid Statuses (`TaskBidStatus` Enum):**
- `pending`: Bid is active and under review by the customer.
- `withdrawn`: Retracted by the provider.
- `rejected`: Decided against by the customer (automatically updated on acceptance of a competing bid).
- `accepted`: Selected by the customer.
- `expired`: Task expired or cancelled.

---

### `task_assignments`
Matches tasks to providers upon bid acceptance.
- `id` (str, primary key): UUID4.
- `task_id` (str, foreign key -> `tasks.id`, unique, index)
- `provider_id` (str, foreign key -> `users.id`, index)
- `accepted_bid_id` (str, foreign key -> `task_bids.id`, nullable)
- `accepted_price` (float)
- `assigned_at` (datetime)
- `started_at` (datetime, nullable)
- `completed_at` (datetime, nullable)
- `status` (TaskAssignmentStatus, enum, default `assigned`): Supports `assigned`, `in_progress`, `completed`, `cancelled`.

---

### `task_status_history`
Tracks transitions for audit logs and analytics.
- `id` (str, primary key): UUID4.
- `task_id` (str, foreign key -> `tasks.id`, index)
- `old_status` (TaskStatus, nullable)
- `new_status` (TaskStatus)
- `changed_by` (str, foreign key -> `users.id`, nullable)
- `timestamp` (datetime)

---

### `task_attachments`
Designed for hosting images and video uploads related to tasks.
- `id` (str, primary key): UUID4.
- `task_id` (str, foreign key -> `tasks.id`, index): Task association.
- `storage_key` (str): Object storage identifier (e.g. S3 file path `tasks/<task-uuid>/image.png`).
- `file_name` (str, nullable): Original uploaded file name.
- `file_size` (int, nullable): Size in bytes.
- `mime_type` (str, nullable): File format content type (e.g., `image/jpeg`, `video/mp4`).
- `url` (str, nullable): Access URL.
- `type` (str, nullable): Workflow context category (e.g., `before_photo`, `after_photo`, `invoice`).
- `created_at` (datetime)

---

## 2. Spatial Query Implementations

To search for tasks within a specific range/radius, the system employs a dual-mode strategy:
1. **PostgreSQL / PostGIS (Production)**:
   Uses `func.ST_DWithin` on the casted `Geography` type of the `geography_point` column for meter-accurate distance queries:
   ```python
   from sqlalchemy import cast
   from geoalchemy2 import Geography
   target_point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)
   statement = statement.where(
       func.ST_DWithin(
           cast(TaskLocation.geography_point, Geography),
           cast(target_point, Geography),
           radius_km * 1000.0
       )
   )
   ```
2. **SQLite (Local / Testing)**:
   Performs a coordinate bounding box math approximation fallback to avoid runtime exceptions without requiring Spatialite:
   ```python
   delta_lat = radius_km / 111.0
   cos_lat = math.cos(math.radians(latitude))
   delta_lng = radius_km / (111.0 * max(cos_lat, 0.1))
   statement = statement.where(
       TaskLocation.latitude >= latitude - delta_lat,
       TaskLocation.latitude <= latitude + delta_lat,
       TaskLocation.longitude >= longitude - delta_lng,
       TaskLocation.longitude <= longitude + delta_lng
   )
   ```

---

## 3. Core API Routes Summary

Integrated under `/api/v1`:

### Tasks Routing (`/tasks`)
- `POST /` - Creates a task. Resolves region ID of the calling user and initializes location geometry and status history.
- `GET /` - List/Search tasks. Supports filtering by category, service, status, search pattern, and coordinate radius bounds.
- `GET /{task_id}` - Retrieve task details.
- `PUT /{task_id}` - Update a task (authorized to posting customer).
- `DELETE /{task_id}` - Cancel a task (transition to `cancelled`).

### Bids Routing
- `POST /tasks/{task_id}/bids` - Submit a bid (restricted to providers). Transitions task status to `bidding` on the first bid.
- `GET /tasks/{task_id}/bids` - Retrieve bids list (task owner sees all, provider sees their own).
- `POST /bids/{bid_id}/withdraw` - Withdraw active bid (restricted to bidder).
- `POST /bids/{bid_id}/accept` - Accept a bid. Automatically rejects other pending bids, transitions task to `assigned`, and inserts a `TaskAssignment` record.