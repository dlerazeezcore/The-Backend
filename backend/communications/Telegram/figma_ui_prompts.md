# Figma UI Prompts For In-App Support

Use these prompts in Figma Make or with your design workflow before wiring the frontend.

## 1. Main Support Chat Screen

Design a mobile-first in-app support chat screen for the Tulip Mobile App.

Requirements:
- Keep the customer entirely inside the app
- No WhatsApp or Telegram branding in the customer-facing UI
- Clean premium travel-tech feel consistent with Tulip
- Top bar title: "Support"
- Optional small status chip: "Usually replies in a few minutes"
- Scrollable message list with clear separation between customer and support bubbles
- Support bubbles should feel human and trusted
- Composer fixed to bottom with multiline text input and send button
- Add empty state for first-time users with a friendly welcome line
- Add loading, sending, failed, and delivered states
- Keep the page thin and easy to wire to backend events later

## 2. Conversation Empty State

Design an empty support conversation state for Tulip Mobile App.

Requirements:
- Headline: "How can we help?"
- Short supportive copy for travel/eSIM issues
- Quick action chips:
  - "Purchase problem"
  - "Activation issue"
  - "Refund question"
  - "General support"
- Keep it modern and lightweight
- Make sure it works on small mobile screens

## 3. Message Status States

Design micro-UI states for in-app support messages in Tulip Mobile App.

Include:
- sending
- sent
- failed with retry action
- support typing placeholder
- unread divider

Keep these subtle and production-friendly, not playful.

## 4. Optional Support Entry Point

Design a compact "Chat with support" entry point for:
- Settings page
- Checkout page
- My eSIMs page

Requirements:
- Should feel native to the app
- Should clearly suggest live help without promising instant response
- Include icon + short label
- Should work as button, card, or list row

## 5. Important Product Constraint

Do not design a customer flow that opens WhatsApp, Telegram, phone dialer, or an external browser.
The customer must stay fully inside Tulip while the support team uses Telegram only on the backend side.
