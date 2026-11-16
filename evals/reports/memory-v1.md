# Multi-session memory v1

Dataset: `evals/datasets/v3/memory-v1.json` (20 author-approved cases).
Provider: `lmstudio` / `qwen3-8b`. Preset: `direct`.

| Mode | Passed | Total |
| --- | ---: | ---: |
| history off, new session | 0 | 20 |
| layers 1 to 4, cross-session | 0 | 20 |
| layers 1 to 4, same session | 20 | 20 |

T3.11: **remains deferred**.

The cross-session mode asks the question in a session that never saw the fact.
The same-session mode is the within-conversation control: it shows whether the
current layers 1 to 4 preserve the fact when the fact and question share a session.

## Failures

### mem-001

- fact: The audit code for the March return is ZX-417.
- question: What audit code did I give you for the March return?
- history_off: ok, answer: 'I don’t have access to your specific audit code or any details about your returns. For assistance with your March return, please contact our support team at support@switchboard.com. They’ll be happy to help!'
- cross_session: ok, answer: 'I don’t have access to your specific audit code or any details about your returns. For assistance with your March return, please contact our support team at support@switchboard.com. They’ll be happy to help!'

### mem-002

- fact: The preferred courier for seller 955fee is Northwind Express.
- question: Which courier did I say seller 955fee prefers?
- history_off: ok, answer: "I don't have access to specific preferences of sellers regarding couriers. For such details, please check directly with Seller 955fee or their customer support. Let me know if you need help with anything else!"
- cross_session: ok, answer: "I don't have access to specific preferences of sellers regarding couriers. For such details, please check directly with Seller 955fee or their customer support. Let me know if you need help with anything else!"

### mem-003

- fact: The internal name for the late-delivery pilot is Project Lantern.
- question: What is the internal name of the late-delivery pilot?
- history_off: ok, answer: 'The internal name of the late-delivery pilot is "Project Velocity." Let me know if you need further details!'
- cross_session: ok, answer: 'The internal name of the late-delivery pilot is "Project Velocity." Let me know if you need further details!'

### mem-004

- fact: The return window for the blue jacket is 12 calendar days.
- question: How many calendar days do I have to return the blue jacket?
- history_off: ok, answer: 'That information is best answered by our Returns team. Would you like me to connect you with them?'
- cross_session: ok, answer: 'That information is best answered by our Returns team. Would you like me to connect you with them?'

### mem-005

- fact: The replacement order id is ORD-88231.
- question: What is the replacement order id?
- history_off: ok, answer: "I don't have access to your order details. Please check your email for confirmation or contact support for assistance."
- cross_session: ok, answer: "I don't have access to your order details. Please check your email for confirmation or contact support for assistance."

### mem-006

- fact: The warehouse contact is Mira Chen.
- question: Who is the warehouse contact?
- history_off: ok, answer: "I'm sorry, but I can't provide specific contact information for warehouses. For assistance with orders or delivery details, please reach out to our customer support team. They’ll be happy to help!"
- cross_session: ok, answer: "I'm sorry, but I can't provide specific contact information for warehouses. For assistance with orders or delivery details, please reach out to our customer support team. They’ll be happy to help!"

### mem-007

- fact: The customer's loyalty tier is Silver Oak.
- question: What loyalty tier did I mention for the customer?
- history_off: ok, answer: "I don't have access to your conversation history. Could you please share more details about the customer or the context of our discussion?"
- cross_session: ok, answer: "I don't have access to your previous messages or conversation history. Could you please provide more context or clarify what you're asking about?"

### mem-008

- fact: The approved refund amount is 47.25 USD.
- question: What refund amount was approved?
- history_off: ok, answer: "I don't have access to specific refund details. For information about your refund, please check your order confirmation email or contact our support team."
- cross_session: ok, answer: "I don't have access to specific refund details. For information about your refund, please check your order confirmation email or contact our support team."

### mem-009

- fact: The escalation code is KITE-9.
- question: What escalation code should I use?
- history_off: ok, answer: 'You can use escalation code **123** for further assistance. Let me know how I can help!'
- cross_session: ok, answer: 'You can use escalation code **123** for further assistance. Let me know how I can help!'

### mem-010

- fact: The pickup window is 14:00 to 16:00 on Friday.
- question: When is the pickup window?
- history_off: ok, answer: 'The pickup window varies depending on your order. For details, please check your confirmation email or message. Let me know if you need help!'
- cross_session: ok, answer: 'The pickup window varies depending on your order. For details, please check your confirmation email or message. Let me know if you need help!'

### mem-011

- fact: The seller's display name is Harbor Goods.
- question: What is the seller's display name?
- history_off: ok, answer: "I'm here to help! For information about sellers, including their display names, please check the product page or contact the seller directly. Let me know if you need assistance with anything else!"
- cross_session: ok, answer: "I'm here to help! For information about sellers, including their display names, I recommend checking the product page or contacting the seller directly. Let me know if you need assistance with anything else!"

### mem-012

- fact: The product SKU for the desk lamp is LMP-330.
- question: What is the SKU for the desk lamp?
- history_off: ok, answer: "I can't provide specific product details like SKUs. For assistance with product information, please contact our data and documents agent."
- cross_session: ok, answer: "I can't provide specific product details like SKUs. For assistance with product information, please contact our data and documents agent."

### mem-013

- fact: The policy exception was approved by Dana Ruiz.
- question: Who approved the policy exception?
- history_off: ok, answer: "I can't provide details about who approved specific policy exceptions. For information on approvals or policies, please consult your organization's records or contact the relevant department. Let me know if you need help with anything else!"
- cross_session: ok, answer: "I don't have access to specific approval details or user-specific information. For questions about approvals or exceptions, please contact your account manager or the relevant team that handles such requests. Let me know how I can assist with anything else!"

### mem-014

- fact: The free-shipping threshold is 89 USD.
- question: What is the free-shipping threshold?
- history_off: ok, answer: 'The free-shipping threshold varies depending on your location and the items in your cart. For specific details, please check your shopping cart or contact our support team.'
- cross_session: ok, answer: 'The free-shipping threshold varies depending on your location and the items in your cart. For specific details, please check your shopping cart or contact our support team.'

### mem-015

- fact: The order was placed on 2026-02-17.
- question: On what date was the order placed?
- history_off: ok, answer: "I don't have access to order details. For information about your order, please check your account or contact customer support."
- cross_session: ok, answer: "I don't have access to order details. For information about your order, please check your account or contact customer support."

### mem-016

- fact: The invoice number is INV-2026-0417.
- question: What is the invoice number?
- history_off: ok, answer: "I don't have access to specific invoice details. Please check your email or contact our support team for assistance."
- cross_session: ok, answer: "I don't have access to specific invoice details. Please check your email or contact our support team for assistance."

### mem-017

- fact: The damaged item was a ceramic mug.
- question: Which item was damaged?
- history_off: ok, answer: "I'm sorry, but I don't have access to specific order details or shipment information. For questions about damaged items, please contact our support team for assistance."
- cross_session: ok, answer: "I'm sorry, but I don't have access to specific order details or shipment information. For questions about damaged items, please contact our support team for assistance."

### mem-018

- fact: The replacement carrier is Bluefin Logistics.
- question: Which carrier handles the replacement?
- history_off: ok, answer: "I'm here to help! For details about carriers or replacements, I recommend reaching out to our support team for assistance. They can provide you with accurate information tailored to your situation. Let me know if there's anything else I can help with! 😊"
- cross_session: ok, answer: "I'm here to help! For details about carriers or replacements, I recommend reaching out to our support team for assistance. They can provide you with accurate information tailored to your situation. Let me know if there's anything else I can help with!"

### mem-019

- fact: The support ticket is TCK-5521.
- question: What is the support ticket number?
- history_off: ok, answer: "I don't have access to your support ticket number. Please check your email or account dashboard for any confirmation messages. Let me know how I can assist you!"
- cross_session: ok, answer: "I don't have access to your support ticket number. Please check your email or account dashboard for any confirmation messages. If you need further assistance, feel free to ask!"

### mem-020

- fact: The internal discount code is ORCHID-12.
- question: What internal discount code did I mention?
- history_off: ok, answer: 'I don’t have access to your personal messages or conversation history. Let me know if you need help with anything else! 😊'
- cross_session: ok, answer: 'I don’t have access to your personal messages or conversation history. Let me know if you need help with anything else! 😊'
