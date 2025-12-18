# Design Guidelines: AI Automation Flow Generator

## Design Approach

**System Selection: Fluent Design System**
This backend AI orchestration platform requires a productivity-focused, data-dense interface. Fluent Design excels at enterprise applications with clear information hierarchy and efficient workflows.

**Rationale:**
- Utility-focused: System monitoring and flow validation are paramount
- Information-dense: Multiple agents, JSON structures, approval states
- Stability-valued: Enterprise tool requiring consistent, reliable interface

---

## Core Design Elements

### Typography
- **Primary Font:** Inter (Google Fonts)
- **Monospace Font:** JetBrains Mono (for JSON display, code blocks)
- **Hierarchy:**
  - H1: 2.5rem/bold - Page titles
  - H2: 1.75rem/semibold - Section headers  
  - H3: 1.25rem/medium - Card titles, agent names
  - Body: 1rem/normal - Content, descriptions
  - Small: 0.875rem/normal - Metadata, timestamps
  - Code: 0.9rem/normal - JSON structures, technical output

### Layout System
**Spacing Units:** Tailwind units of **2, 4, 8, 12, 16**
- Component padding: p-4 to p-8
- Section gaps: gap-8 to gap-12
- Card spacing: p-6
- Tight elements: gap-2, gap-4

---

## Component Library

### Navigation
- **Left Sidebar (Fixed):** w-64, Agent status indicators, quick actions
- **Top Bar:** Full-width, breadcrumbs, API status badge, settings icon

### Core UI Elements

**Agent Status Cards:**
- Grid layout (grid-cols-2 gap-4)
- Each card shows: Agent name, status badge, last execution time
- Status indicators: Processing/Idle/Error states

**Flow Input Panel:**
- Large textarea for prompt input (min-h-32)
- Character counter
- Primary action button: "Generate Flow"
- Clear/Reset secondary button

**Response Viewer:**
- Tabbed interface: Intent / Flow / Validation / Learning Log
- JSON syntax highlighting containers
- Collapsible sections for nested structures

**Approval/Rejection Display:**
- Status banner: Full-width, prominent success/error states
- Error list: Bulleted, clear messaging
- Retry/Edit actions

### Data Displays
- **Flow Visualization:** JSON tree view with expand/collapse
- **Learning Log Table:** Sortable columns (timestamp, prompt, status, errors)
- **Metrics Dashboard:** Agent success rates, average processing time, total flows

### Forms
- Input fields: Minimal borders, focus states with subtle accent
- Labels: Medium weight, positioned above inputs
- Validation: Inline error messages below fields

### Overlays
- **Modal for Settings:** API key configuration, agent parameters
- **Toast Notifications:** Top-right corner, auto-dismiss for confirmations

---

## Animations
**Minimal approach:**
- Agent status pulse for "processing" state only
- Smooth transitions for tab switching (duration-200)
- No decorative animations

---

## Layout Structure

**Dashboard View:**
1. Top bar with system status
2. Left sidebar with agent overview
3. Main content area (max-w-7xl):
   - Flow generator panel (w-full)
   - Response viewer (w-full, mt-8)
   - Learning history table (w-full, mt-12)

**Responsive Behavior:**
- Desktop: Sidebar visible, multi-column metrics
- Tablet/Mobile: Collapsed sidebar (hamburger), single column, stack all sections

---

## Images
No hero images or decorative imagery needed. This is a pure utility interface focused on data visualization and workflow management.