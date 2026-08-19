# Diagnostic Quickstart Prototypes

## Purpose

These prototypes explore how a rider starts a new Bike Doc AI session from the Android Home screen after choosing the bike they are working on.

The flow was created to answer two related product questions:

1. How can a rider move directly from Home into useful help without being asked to select a bike again?
2. How should the entry experience grow beyond problem diagnosis to support other kinds of assistance, such as a full-bike check or guidance through a known repair?

The prototypes cover only the Home-to-session handoff and the opening state of the resulting conversation. They do not attempt to define the rest of a diagnostic, checkup, or repair session.

## Directions explored

- [Prototype 1 — Mode picker sheet](./01-mode-picker.html): selecting the Home action opens a bottom sheet where the rider chooses the type of help before entering the session.
- [Prototype 2 — Conversational router](./02-conversational-router.html): selecting the Home action enters a neutral conversation immediately, and Bike Doc asks the rider what kind of help they need.
- [Prototype 3 — Home quick compose](./03-home-quick-compose.html): selecting the Home action expands a compact setup area in place, where the rider chooses the type of help and can provide useful starting context before entering the session.

## Selected direction

**Prototype 3 — Home quick compose was selected.**

This direction keeps the rider on Home while they make the few decisions needed to start well. It preserves the quick path introduced by the Home bike selector, makes the available kinds of assistance explicit, and gives Bike Doc useful context before the conversation begins.

The selected approach balances immediacy with clarity:

- The bike is chosen once on Home and carried into the new session.
- Session creation is framed around what the rider wants to accomplish, rather than treating every entry as a generic repair.
- A short description is encouraged where it improves the first response, but the setup remains compact.
- The rider can see and change their selections before committing to the session.
- New session types can be added to the same conceptual entry point in the future.

## Selected flow

### 1. Home establishes context

The screen opens with a lightweight Bike Doc app bar, followed by a personalized introduction:

- The eyebrow, **“Ready when you are,”** communicates availability without suggesting that something is already wrong.
- The greeting, **“Hi, Maya,”** provides the strongest piece of personal hierarchy.
- The supporting sentence explains the complete task in plain language: choose the bike, then tell Bike Doc where to start.

This introduction positions Bike Doc as an ongoing assistant rather than a single-purpose diagnostic form.

### 2. Bike selection is a compact contextual row

The selected bike appears inside a white rounded surface labeled **“Working on.”** The neutral wording applies equally to diagnosis, inspection, and repair.

The row contains:

- A prominent bike selector showing the current bike.
- Existing bikes and **“New bike”** as possible selections.
- A quiet **“My bikes”** text action beside the selector for bike management.

Bike selection is presented as session context, not as a separate navigation step. Bike management remains accessible without competing with the main session-start action.

The prototype opens with **“Maya’s Trek FX 3”** as realistic sample data. This is a representative populated state rather than a requirement that an existing bike always be selected by default.

### 3. Home initially presents two clear paths

Below the bike row, the collapsed Home state has two actions:

- **“Start with Bike Doc”** is the filled primary action.
- **“Resume a session”** is the outlined secondary action.

The primary label is intentionally broader than “Start New Repair.” It remains accurate for diagnosis, full-bike checks, known repairs, and additional AI-assisted session types that may be introduced later.

Starting something new and continuing earlier work remain visually and conceptually separate.

### 4. Starting expands an inline quick composer

Selecting **“Start with Bike Doc”** expands a setup card directly below the bike row. The start and resume actions are replaced by this card while setup is active, keeping the rider focused on one task and preventing competing actions from cluttering the screen.

The card is titled **“Start a session”** and uses the supporting line **“Give Bike Doc just enough context to begin.”** This sets the expectation that setup will be brief.

A circular close control returns to the collapsed Home state. The setup therefore feels reversible and does not require committing to a new screen merely to review the options.

### 5. Session type uses a compact segmented choice

Three equally weighted choices appear together:

- **Diagnose** — determine the cause of a symptom or unexpected behavior.
- **Checkup** — perform a broader safety and condition review.
- **Repair** — get guidance for a repair the rider already knows they want to perform.

Each choice combines a small symbol with a short label. The active choice is shown on a raised white segment with dark blue text, while inactive choices sit quietly on the shared pale surface.

Diagnosis is the initial selection in the prototype because it represents the current primary flow. The three choices are nevertheless given equal space so the control does not imply that the future modes are lesser or hidden features.

### 6. Starting context adapts to the chosen mode

The prompt beneath the segmented choice changes with the selected session type.

#### Diagnose

- Prompt: **“What’s happening?”**
- Example: **“The chain skips under load.”**
- Guidance explains that a short symptom helps Bike Doc ask a better first question.
- Action: **“Begin diagnose session.”**

#### Checkup

- Prompt: **“Anything you’re concerned about? (optional)”**
- Example: **“Getting ready for a long ride.”**
- Guidance explicitly allows the rider to leave the field blank and begin a standard safety-first inspection.
- Action: **“Begin checkup session.”**

#### Repair

- Prompt: **“What repair are you planning?”**
- Example: **“Replace the rear tube.”**
- Guidance previews that Bike Doc will confirm tools, parts, and safety before beginning.
- Action: **“Begin repair session.”**

Changing modes clears the previous detail in the prototype. This prevents text written for one purpose from being accidentally carried into a different kind of session.

The multiline field is intentionally short. It invites one useful starting detail without making Home feel like a form or requiring the rider to provide a complete history before they can receive help.

### 7. The session begins with visible continuity

After the rider begins, the conversation screen preserves the choices made on Home:

- The app bar title reflects the selected session type.
- The selected bike appears directly beneath the title.
- A pale green summary card repeats the session type, bike, and starting detail.
- When the rider supplied text, it appears as their first message.
- Bike Doc’s first response is tailored to the chosen session type and acknowledges any supplied context.

This continuity confirms that the setup was understood and avoids making the rider repeat themselves. It also makes the session’s purpose easy to recover at a glance after an interruption.

The conversation ends this prototype with a persistent composer labeled **“Reply or add a photo…”**, reinforcing that text and visual evidence can both continue the interaction.

## Visual design and styling

### Design character

The selected design is calm, capable, and practical. It uses restrained color, rounded Material-style surfaces, clear action hierarchy, and compact mobile spacing. The interface should feel like a trustworthy workshop companion rather than an automotive dashboard, marketing page, or generic chatbot.

### Color palette

- Deep blue-green (`#1B3A4B`) is the primary brand and action color. It is used for the main Home action, selected-mode text, the rider’s chat bubbles, navigation controls, and send action.
- Workshop green (`#2D6A4F`) is the supportive accent. It is used for the availability eyebrow, the final setup action, and the Bike Doc avatar.
- Warm brown (`#7A4E2D`) remains available as a tertiary brand color, although this focused flow does not need to use it prominently.
- The app background is an off-white (`#F8FAF9`) rather than stark white.
- Primary surfaces are white, while secondary surfaces use pale green-gray tones such as `#E9F0ED`, `#EEF2F0`, and `#E5F1EB`.
- Primary text uses near-black (`#172126`); supporting text uses muted blue-gray (`#5C696F`).
- Borders use quiet gray-green tones so grouping remains visible without making every component visually heavy.

Color is semantic rather than decorative: deep blue-green identifies primary rider actions, green identifies supportive Bike Doc guidance, and pale surfaces communicate grouping and context.

### Typography

- Typography is Android-oriented and uses Roboto as the preferred face, with neutral system sans-serif fallbacks.
- The hierarchy moves from a 29px personalized greeting to a 20px setup-card title, 14px body text, and compact 10–12px labels and supporting copy.
- Headings use moderately strong weight and tight spacing rather than oversized display typography.
- Labels and actions use heavier weight for clarity at small sizes.
- The uppercase eyebrow uses additional letter spacing to act as a quiet status signal.

### Layout and spacing

- The app content uses approximately 20–22px horizontal padding and a consistent compact vertical rhythm.
- The bike context is grouped into one row instead of being scattered across separate label, selector, and management controls.
- Cards use 15–17px internal padding, leaving enough space to feel tactile without consuming excessive vertical room.
- Controls maintain roughly 48–54px heights for comfortable mobile targets.
- The setup card remains within the Home content column and the page can scroll when the available phone height is limited.
- The chat uses 16–18px content padding and keeps message bubbles to roughly four-fifths of the available width for readable conversation rhythm.

### Shape and elevation

- Major cards use generous 18–22px corner radii.
- Buttons use 14–16px radii rather than fully pill-shaped treatment.
- Inputs use slightly tighter 12–13px radii to remain recognizable as controls.
- Small circular controls are reserved for close, back, menu, and send actions.
- Borders define most surfaces. Shadows are subtle and used selectively for the primary action, selected segment, and expanded setup card.
- The setup card receives the strongest elevation because it is the current focus layered onto Home.

### Motion and state changes

- Screen changes use a short fade with slight vertical movement.
- The setup card expands with a roughly 220ms ease-out motion, combining a small upward shift and scale change.
- Pressing the main action provides a restrained scale response.
- Motion communicates hierarchy and continuity; it is not used as decoration.

### Action hierarchy

- Filled deep blue-green identifies the primary entry action.
- Outlined treatment identifies resume as an available but secondary path.
- Green identifies the final commitment within session setup.
- Text-only treatment keeps bike management available without competing with starting a session.
- The selected segmented option uses surface elevation rather than another saturated fill, keeping the small control legible and calm.

### Conversation styling

- Bike Doc messages use pale neutral-green bubbles with dark text.
- Rider messages use deep blue-green bubbles with white text.
- Bubble corners distinguish speaker direction while retaining a shared rounded visual language.
- Bike Doc is identified by a compact green **“BD”** avatar.
- The session summary uses a pale green contextual card rather than a chat bubble because it describes session state, not dialogue.
- The composer sits on a white surface separated by a subtle top border and uses a circular filled send action.

## Differences from the current Android Home screen

The current app provides the necessary functionality in a simple vertical sequence: **My Bikes**, a **“Repairing”** label, a separate outlined bike selector, **“Start New Repair,”** and **“Resume Repair.”** Starting a new repair immediately enters a diagnostic session for the selected bike.

The selected prototype changes that experience in the following ways:

| Current app | Selected prototype |
| --- | --- |
| Home is primarily a vertical stack of independent controls. | Related controls are grouped into clear surfaces with deliberate hierarchy. |
| The app bar exposes a filled **“Menu”** button. | The app bar uses a quiet overflow icon so navigation does not compete with the main task. |
| **“My Bikes”** is a separate filled action near the top of the content. | **“My bikes”** becomes a low-emphasis text action attached to the bike selector. |
| The selected-bike context is labeled **“Repairing.”** | It is labeled **“Working on,”** which also fits diagnosis and inspection. |
| The selector is visually separate from bike management. | Selection and management are grouped into one compact bike-context row. |
| The primary action is **“Start New Repair.”** | The future-compatible primary action is **“Start with Bike Doc.”** |
| Starting immediately assumes a diagnostic repair flow. | Starting expands a reversible setup card before entering a session. |
| There is one new-session type. | Diagnose, Checkup, and Repair are explicit peer choices. |
| The rider provides context only after entering chat. | The rider can provide one useful line of context on Home. |
| The opening conversation is identified generically as a diagnostic chat. | The conversation title, summary, rider message, and first Bike Doc response reflect the selected purpose. |
| Functional styling relies mostly on default component placement. | Styling establishes a branded hierarchy through cards, restrained elevation, semantic color, rounded shapes, and purpose-specific copy. |

The prototype retains the current branch’s most important interaction improvement: the rider selects a bike on Home and is not sent through a second bike-selection flow before starting.

## Prototype-only presentation

When opened on a wide display, the HTML artifact shows the experience inside a phone frame with reviewer notes and a reset control beside it. These elements exist only to make the design easy to evaluate and are not part of the selected Android UI.

The sample name, bike, symptom, status-bar content, and opening replies demonstrate the intended hierarchy and continuity. Final product copy and example data may vary while preserving the decisions documented above.

## Out of scope

This design selection does not define:

- The remainder of any diagnosis, checkup, or repair conversation.
- Resume-session selection and history screens.
- Bike-management screens or the complete new-bike experience.
- Loading, empty, error, permission, or offline states.
- Photo capture and attachment interactions beyond the composer affordance.
- The final taxonomy or number of future session types.
- Technical implementation details.
