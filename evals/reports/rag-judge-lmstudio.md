# Rag eval: lmstudio

## Run

- Dataset: `v3` at `evals/datasets/v3/rag.jsonl`
- Split: `test`, scored 40 of 40 cases in the split (40 in the dataset)
- Orchestrator: `langgraph`; provider: `lmstudio`; model: `qwen3-8b`
- Generated: 2026-11-13T19:07:14.366135+00:00
- Wall time: 182.265 s

## Metrics

| Metric | Value |
| --- | --- |
| recall_at_5_query_as_written | 1.0000 |
| mrr_query_as_written | 0.8250 |
| recall_at_5_always_rewrite | 0.9750 |
| mrr_always_rewrite | 0.8229 |
| recall_at_5_conditional_rewrite | 1.0000 |
| mrr_conditional_rewrite | 0.8354 |
| recall_at_5_conditional_hybrid | 1.0000 |
| mrr_conditional_hybrid | 0.8354 |
| judge_accept_rate | 0.9750 |

## Strategies

| Strategy | Recall@5 | MRR | Cases | Rewrite rounds | Retrieval rounds | Cases with rewrite | p50 ms | p95 ms | Errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| query_as_written | 1.0000 | 0.8250 | 40 | 0 | 40 | 0 | 94.0 | 436.8 | 0 |
| always_rewrite | 0.9750 | 0.8229 | 40 | 40 | 40 | 40 | 1395.5 | 6833.85 | 0 |
| conditional_rewrite | 1.0000 | 0.8354 | 40 | 4 | 40 | 4 | 101.0 | 725.05 | 0 |
| conditional_hybrid | 1.0000 | 0.8354 | 40 | 4 | 40 | 4 | 116.0 | 908.3 | 0 |

Cases tagged `needs_rewrite`:

| Strategy | Cases | Recall@5 | MRR |
| --- | --- | --- | --- |
| query_as_written | 9 | 1.0000 | 0.6667 |
| always_rewrite | 9 | 1.0000 | 0.7130 |
| conditional_rewrite | 9 | 1.0000 | 0.7130 |
| conditional_hybrid | 9 | 1.0000 | 0.7130 |

All four strategies run on the same cases, in the same order. Recall@5 counts the share of the labeled chunk keys in the first five results; MRR is the mean of 1/rank of the first relevant result. A chunk's score is always its cosine similarity, so the rewrite threshold and the fused order stay separate. The conditional strategies rewrite only when history is present or the best cosine is below the tenant's `retrieval.min_score`, and never make more than two retrieval rounds. The latency columns are wall time per case for that strategy, including the embedding call and any rewrite call.

Cross-encoder re-ranking is not enabled: it is optional in design.md section 9.2, would add sentence-transformers and a torch stack on an 8 GB GPU shared with qwen3-8b and bge-m3, and this run has no measured evidence that it would pay for that cost.

## Answer judge

Judge: llm (lmstudio/qwen3-8b). Each case generated one answer from the `conditional_hybrid` retrieval result and the judge scored it against the evidence. A judge verdict never changes the retrieval metrics or the `passed` flag above; judge mode is opt-in with `--judge`.

| Cases judged | Acceptable | Weak | Errors | Accept rate |
| --- | --- | --- | --- | --- |
| 40 | 39 | 1 | 0 | 0.9750 |

Weak case ids: rag008. Judge error ids: none.

Answer generation p50 2695.5 ms, judge call p50 2861.5 ms. The latency table below stays retrieval only, so it is comparable to a plain rag report. Retrieval numbers in this report come from this run: the rewrite calls are model output, so they can differ slightly from the committed rag report. The judge metric is what judge mode adds.

| Case | Verdict | Score | Answer | Reason |
| --- | --- | --- | --- | --- |
| rag001 | acceptable | 1.00 | If you changed your mind, you have 30 calendar days from the delivery date to send the item back [d2]. The refund is issued within five business days after inspection once the returned item arrives at the warehouse [d1]. | The answer correctly cites the evidence (d1 and d2) to support the claims about the return window and refund processing time. |
| rag002 | acceptable | 1.00 | 电子产品可以退货，但有一定的限制。未开封的电子产品可以在交付后的15个日历日内凭任何原因退货，而已开封的电子产品仅在存在缺陷的情况下才能退货，并且客户需在退货请求中描述缺陷 [d1]。 | The answer truthfully reflects the information provided in the evidence [d1], which specifies the return policy for electronics. |
| rag003 | acceptable | 1.00 | Whether you can return the item depends on its type and when you request the return. If it is an electronic product, you can return it within 15 calendar days from delivery [d3]. For non-electronic items, the standard return window is 30 calendar days from delivery [d2]. | The answer correctly cites the return policies for electronic and non-electronic items based on the provided evidence. |
| rag004 | acceptable | 1.00 | Yes, you can get your cash back after returning something, provided the refund is issued to the original payment method within five business days after inspection [d2]. However, shipping fees are refunded only if the return is caused by a defect or a wrong item, and the original delivery time is not refunded for a change of mind [d2]. | The answer truthfully reflects the information from the evidence [d2], including the refund process and conditions for shipping fees. |
| rag005 | acceptable | 1.00 | According to the returns policy, personalized items cannot be returned unless they arrive damaged or defective [d1]. The documents do not specify whether a personalized gift is considered a personalized item. | The answer truthfully states that personalized items cannot be returned unless damaged or defective based on the evidence provided. |
| rag006 | acceptable | 1.00 | You must report a damaged item within 48 hours of delivery with a photo [d1]. If you do not report it within this timeframe, the warehouse may refuse the return [d1]. | The answer correctly cites the evidence (d1) and states that damaged items must be reported within 48 hours of delivery with a photo, which is supported by the provided document. |
| rag007 | acceptable | 1.00 | To contact about a specific order, customers can ask questions in the order page, and the support team answers within one business day [d2]. For late delivery compensation requests, customers open the order page and choose "Request late delivery compensation," providing the order ID and a short description [d1]. | The answer truthfully addresses the question by citing relevant evidence (d1 and d2) and provides accurate information about contacting support for an order. |
| rag008 | weak | 0.50 | Yes, you need the original packaging to return an item, as the item must be unused and in its original packaging [d1]. If the item is electronics, it also needs to be unopened unless it is defective [d5]. | The answer claims that original packaging is required for returns and mentions electronics needing to be unopened unless defective, but the evidence does not explicitly state that original packaging is always required. The evidence only states that items must be unused and in their original packaging, which may not apply universally. |
| rag009 | acceptable | 1.00 | Return shipping fees are refunded when the return is caused by a defect or a wrong item [d1]. If the return is due to a change of mind, the original delivery time is not refunded, but it does not explicitly mention whether shipping fees are refunded in this case [d1]. | The answer truthfully reflects the information from the evidence, stating that return shipping fees are refunded when the return is caused by a defect or a wrong item and noting the lack of explicit mention for change of mind returns. |
| rag010 | acceptable | 1.00 | If you return an item without opening a return request, the item may be refused at the warehouse [d2]. The return policy requires that a return request be opened in the order page before proceeding with the return [d1]. | The answer truthfully reflects the information from the evidence, citing relevant documents d1 and d2. |
| rag011 | acceptable | 1.00 | Standard delivery in the southeast region takes three to seven business days [d1]. The exact time may vary depending on the carrier and the specific location within the southeast region. | The answer truthfully cites the evidence from document d1 and provides a clear response based on the information provided. |
| rag012 | acceptable | 1.00 | Express delivery takes one to three business days [d1]. The exact speed may vary depending on the seller and location, as these ranges are estimates, not guarantees [d1]. | The answer accurately reflects the information provided in the evidence document d1, which states that express delivery takes one to three business days. |
| rag013 | acceptable | 1.00 | A seller must hand the parcel to the carrier within two business days after payment approval [d1]. If the order is paid by boleto, the handling clock starts only after bank confirmation [d1]. | The answer correctly cites the evidence [d1] and accurately reflects the information provided about the handling time for sellers. |
| rag014 | acceptable | 1.00 | The parcel has not moved for two days, which is within the allowed handling time [d1]. If it still hasn't moved after the estimated delivery date, it may be considered late, and you can request compensation if it arrives at least three calendar days past the estimated date [d2][d3]. Check the tracking code for updates, as delays can occur due to various factors like weather or customs checks [d4]. | The answer truthfully addresses the question based on the provided evidence, explaining that a two-day delay is within allowed handling time and outlining next steps if the parcel remains delayed. |
| rag015 | acceptable | 1.00 | If your parcel shows as delivered but you cannot find it, contact the carrier first, as they may have information about the delivery status [d1]. If the issue persists, check if there were any delays due to weather, strikes, or customs checks that might affect the delivery [d3]. | The answer truthfully addresses the question by citing relevant evidence (d1 and d3) and provides appropriate steps for the customer to take. |
| rag016 | acceptable | 1.00 | You can change the delivery address until the seller hands the parcel to the carrier [d1]. If the address is incorrect and causes a delay, the customer may not be eligible for late-delivery compensation [d2]. | The answer truthfully references the evidence to explain that address changes are possible until the seller hands the parcel to the carrier and outlines conditions under which late-delivery compensation may not apply. |
| rag017 | acceptable | 1.00 | Yes, you can request cancellation of an order that never arrived after 21 days from the estimated delivery date [d1]. However, if you wish to claim compensation for late delivery, the parcel must arrive at least three calendar days after the estimated date and the request must be made within 60 calendar days of the delivery [d2]. | The answer truthfully reflects the information in the evidence, explaining that cancellation is possible after 21 days and outlining conditions for late delivery compensation. |
| rag018 | acceptable | 1.00 | The estimated delivery date is calculated based on the destination region, the seller's handling time, and the carrier's service level [d3]. It is displayed on the order page as an estimate, not a guarantee [d4]. The actual delivery date is determined by the timestamp recorded by the carrier when the customer receives the parcel [d2]. | The answer truthfully explains how the estimated delivery date is calculated based on the destination region, seller's handling time, and carrier's service level, which is supported by the evidence [d3]. It also correctly notes that the actual delivery date is determined by the carrier's timestamp, as stated in [d2]. |
| rag019 | acceptable | 1.00 | A boleto payment delay handling starts only after the bank confirms the payment, which may take up to the next business day [d2]. If the seller does not process the order within two business days of this confirmation, they may receive a late shipment warning [d1]. | The answer correctly references the evidence to explain that boleto payment delay handling starts after bank confirmation and mentions the late shipment warning if the seller does not process the order within two business days. |
| rag020 | acceptable | 1.00 | A delivery is considered late when the parcel reaches the customer after the estimated delivery date shown on the order page [d1]. The delivery is eligible for compensation if it arrives at least three calendar days after the estimated date, provided the order is not canceled and the account is in good standing [d2]. | The answer accurately reflects the information from the provided evidence [d1] and [d2], correctly stating that a delivery is considered late if it arrives after the estimated date and is eligible for compensation if it arrives at least three calendar days late. |
| rag021 | acceptable | 1.00 | 延迟送达有补偿，但需满足一定条件。例如，如果包裹在预计送达日期后至少三天到达，并且订单未被取消、账户状态良好，客户可在60天内申请补偿 [d1]。此外，若因天气、罢工或海关检查导致的延误，也可能符合补偿政策 [d3]。 | The answer truthfully reflects the information in the evidence, including the conditions for compensation and examples of eligible delays. |
| rag022 | acceptable | 1.00 | The late delivery voucher is worth ten percent of the item value, with a minimum of five reais and a maximum of fifty reais [d2]. | The answer correctly cites evidence [d2] which explicitly states the late delivery voucher is worth ten percent of the item value, with a minimum of five reais and a maximum of fifty reais. |
| rag023 | acceptable | 1.00 | To request late delivery compensation, the customer opens the order page and chooses "Request late delivery compensation," providing the order ID and a short description [d1]. The request must be made within 60 calendar days of the delivery, and the delivery must arrive at least three calendar days after the estimated delivery date [d2]. | The answer truthfully summarizes the process and requirements for requesting late delivery compensation based on the provided evidence. |
| rag024 | acceptable | 1.00 | If an order has several items, you receive one voucher per order, regardless of the number of items [d2]. The voucher is equal to ten percent of the item value, with a minimum of five reais and a maximum of fifty reais [d1]. | The answer truthfully states that one voucher is received per order regardless of the number of items, and correctly cites evidence d2 for this claim. |
| rag025 | acceptable | 1.00 | A refused parcel is not eligible for late compensation, as stated in the exclusions section [d3]. The policy specifies that a delivery is not eligible if the customer refused the parcel. | The answer correctly cites evidence [d3] which explicitly states that a delivery is not eligible if the customer refused the parcel, directly answering the question. |
| rag026 | acceptable | 1.00 | If your address was wrong and caused a delay, the delivery is not eligible for late-delivery compensation because delays caused by an incorrect address supplied by the customer are excluded [d3]. You should check if the parcel arrived at least three calendar days after the estimated delivery date and request compensation within 60 calendar days of the delivery [d1]. | The answer correctly references the evidence to explain that incorrect customer addresses disqualify late-delivery compensation and outlines the steps for requesting compensation. |
| rag027 | acceptable | 1.00 | You can use credit cards, boleto, and Pix as payment methods [d1]. The available methods may depend on the seller and the order value [d1]. Credit card payments are approved immediately in most cases [d3]. | The answer truthfully cites the available payment methods from the evidence and correctly notes that availability may depend on the seller and order value. |
| rag028 | acceptable | 1.00 | Yes, you can split the payment into installments if the order amount is above 50 reais, with up to 12 interest-free installments depending on the seller [d1]. The checkout will show the monthly amount and total before confirmation. | The answer truthfully reflects the information provided in the evidence [d1], which confirms that orders above 50 reais can be split into installments with the number depending on the seller. |
| rag029 | acceptable | 1.00 | A boleto takes one business day to confirm, as it must be paid before its due date, usually the next business day [d1]. The order is released to the seller after the payment is confirmed. The handling time for orders paid by boleto starts only after bank confirmation [d2]. | The answer correctly states that a boleto takes one business day to confirm based on the evidence from d1, and it accurately mentions that handling time starts after bank confirmation as per d2. |
| rag030 | acceptable | 1.00 | If you paid with Pix, your refund is returned to the account that made the payment [d1]. The refund process typically takes up to five business days after the item has been inspected upon return [d3]. | The answer truthfully cites the evidence that refunds for Pix payments are returned to the original account and mentions the typical refund processing time based on document d3. |
| rag031 | acceptable | 1.00 | A chargeback opens a dispute, and the seller is asked for evidence. The amount is held until the dispute is resolved [d1]. | The answer truthfully reflects the information provided in the evidence [d1], which explains that a chargeback opens a dispute and the seller is asked for evidence with the amount held until resolved. |
| rag032 | acceptable | 1.00 | Support will never ask for your card password by email or chat [d1]. The payment page uses the marketplace checkout and does not request card passwords through any communication channels. | The answer truthfully states that support will never ask for a card password, supported by the evidence provided. |
| rag033 | acceptable | 1.00 | A credit card verification typically takes up to one business day if additional verification is required [d1]. The payment itself is approved immediately in most cases. | The answer truthfully reflects the information in the evidence [d1], which states that additional verification can take up to one business day. |
| rag034 | acceptable | 1.00 | Any registered company with a valid tax ID can apply to sell on the marketplace [d1]. The application process requires providing the company's legal name, tax ID, and contact phone number to create a seller account [d5]. | The answer truthfully states that any registered company with a valid tax ID can apply to sell on the marketplace and mentions the required information for creating a seller account, both supported by the provided evidence. |
| rag035 | acceptable | 1.00 | To become a seller, you need to provide your company legal name, tax ID, and contact phone number [d1], along with a company registration document, proof of address, and bank account details that match the tax ID [d3]. Additionally, you must accept the product listing rules, returns policy, and shipping policy before publishing [d4]. | The answer truthfully cites the required documents and policies based on the provided evidence. |
| rag036 | acceptable | 1.00 | The platform keeps the marketplace commission, which is twelve percent of the item value, deducted before the payout [d3]. It does not specify what else the platform retains from each sale. | The answer truthfully states that the platform keeps the marketplace commission (twelve percent of the item value) based on evidence [d3], and correctly notes that no other information is specified. |
| rag037 | acceptable | 1.00 | Payouts are made every two weeks for delivered orders whose return window has closed [d1]. A new seller may experience a longer hold period until the first ten orders are delivered [d1]. | The answer correctly states that payouts are made every two weeks for delivered orders whose return window has closed, and mentions the longer hold period for new sellers based on the provided evidence. |
| rag038 | acceptable | 1.00 | Yes, a person can hold more than one seller account, but only with written approval [d1]. The documents do not specify any additional conditions or restrictions regarding having multiple seller accounts. | The answer truthfully states that a person can hold more than one seller account with written approval, citing the correct evidence (d1). |
| rag039 | acceptable | 1.00 | Before a listing is published, it must include a clear title, at least one photo, product dimensions, warranty terms, and comply with the product listing rules, returns policy, and shipping policy [d1]. Prohibited items cannot be listed [d1]. | The answer truthfully reflects the information provided in the evidence [d1], which explicitly states that a listing must include a clear title, at least one photo, product dimensions, warranty terms, and comply with the product listing rules, returns policy, and shipping policy. It also mentions prohibited items cannot be listed. |
| rag040 | acceptable | 1.00 | You can ask the onboarding team if you have questions about seller onboarding [d1], or the support team if you have questions about a specific order [d2]. The documents do not specify who to contact for other types of inquiries. | The answer truthfully cites the onboarding team and support team based on the provided evidence and correctly states that other inquiries are not specified in the documents. |

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Retrieval | 1760.0 | 7136.1 | 802.0 | 9667.0 | 2515.025 |
| Runner wall | 7881.5 | 14892.55 | 4604.0 | 18777.0 | 9009.3 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 70470 | 0 | 5258 | 75728 |

Cost: USD 0.000000 for this run. The provider is local (LM Studio), so the run has no API cost; the price table records zero for it on purpose.

## Cases (40)

| Case | Query | Needs rewrite | Relevant | query_as_written R@5/MRR | always_rewrite R@5/MRR | conditional_rewrite R@5/MRR | conditional_hybrid R@5/MRR | Rewrites | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| rag001 | I changed my mind, how long do I have to send it back? | yes | returns-policy.md#1 | 1.000/0.500 | 1.000/0.333 | 1.000/0.500 | 1.000/0.500 | 0/1/0/0 |  |
| rag002 | 电子产品可以退货吗？ | yes | returns-policy.md#2 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag003 | Can I still return it? | yes | returns-policy.md#2 | 1.000/0.500 | 1.000/0.333 | 1.000/0.333 | 1.000/0.333 | 0/1/1/1 |  |
| rag004 | Can I get my cash back after returning something? | yes | returns-policy.md#3, payments.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag005 | Can I return a personalized gift? | no | returns-policy.md#4 | 1.000/1.000 | 1.000/0.500 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag006 | How quickly must I report an item that arrived damaged? | no | returns-policy.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag007 | Who do I contact about a specific order? | no | returns-policy.md#5 | 1.000/0.500 | 1.000/1.000 | 1.000/0.500 | 1.000/0.500 | 0/1/0/0 |  |
| rag008 | Do I need the original packaging to return something? | no | returns-policy.md#0 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag009 | Are return shipping fees refunded? | no | returns-policy.md#3 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag010 | What happens if I return an item without opening a request? | no | returns-policy.md#4 | 1.000/0.500 | 1.000/0.500 | 1.000/0.500 | 1.000/0.500 | 0/1/0/0 |  |
| rag011 | How long does standard delivery take in the southeast? | no | shipping-policy.md#2 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag012 | How fast is express delivery? | no | shipping-policy.md#2 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag013 | When must a seller hand the parcel to the carrier? | no | shipping-policy.md#1 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag014 | It has not moved for two days, what now? | yes | shipping-policy.md#3 | 1.000/0.333 | 1.000/0.250 | 1.000/0.250 | 1.000/0.250 | 0/1/1/1 |  |
| rag015 | The parcel says delivered but I cannot find it, what should I do? | no | shipping-policy.md#3 | 1.000/1.000 | 1.000/0.500 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag016 | Can I change the delivery address after the seller shipped it? | no | shipping-policy.md#5 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag017 | Can I cancel an order that never arrived after 21 days? | no | shipping-policy.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag018 | How is the estimated delivery date calculated? | no | shipping-policy.md#0 | 1.000/0.333 | 1.000/0.500 | 1.000/0.333 | 1.000/0.333 | 0/1/0/0 |  |
| rag019 | Does a boleto payment delay handling? | no | shipping-policy.md#1, payments.md#3 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag020 | When is a delivery considered late? | no | late-delivery-compensation.md#0 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag021 | 延迟送达有补偿吗？ | yes | late-delivery-compensation.md#1 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag022 | How much is the late delivery voucher worth? | no | late-delivery-compensation.md#2 | 1.000/0.500 | 0.000/0.000 | 1.000/0.500 | 1.000/0.500 | 0/1/0/0 |  |
| rag023 | How do I request late delivery compensation? | no | late-delivery-compensation.md#3 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag024 | How many vouchers do I get if an order has several items? | no | late-delivery-compensation.md#3 | 1.000/0.500 | 1.000/0.500 | 1.000/0.500 | 1.000/0.500 | 0/1/0/0 |  |
| rag025 | Is a refused parcel eligible for late compensation? | no | late-delivery-compensation.md#4 | 1.000/0.333 | 1.000/0.500 | 1.000/0.333 | 1.000/0.333 | 0/1/0/0 |  |
| rag026 | What happens if my address was wrong and the parcel was late? | no | late-delivery-compensation.md#4, late-delivery-compensation.md#1 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag027 | Which payment methods can I use? | no | payments.md#0 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag028 | Can I split the payment? | yes | payments.md#2 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/1/1 |  |
| rag029 | How long does a boleto take to confirm? | no | payments.md#3 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag030 | Where does my refund go if I paid with Pix? | no | payments.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag031 | What happens in a chargeback dispute? | no | payments.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag032 | Will support ever ask for my card password? | no | payments.md#5 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag033 | How long does a credit card verification take? | no | payments.md#1 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag034 | Which companies can apply to sell on the marketplace? | no | seller-onboarding.md#0 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag035 | What documents do I need to become a seller? | no | seller-onboarding.md#2 | 1.000/0.333 | 1.000/0.500 | 1.000/0.333 | 1.000/0.333 | 0/1/0/0 |  |
| rag036 | What does the platform keep from each sale? | yes | seller-onboarding.md#4 | 1.000/0.333 | 1.000/0.500 | 1.000/0.333 | 1.000/0.333 | 0/1/0/0 |  |
| rag037 | How often are seller payouts made? | no | seller-onboarding.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag038 | Can I have more than one seller account? | no | seller-onboarding.md#1 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag039 | What must a listing include before it is published? | no | seller-onboarding.md#3 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag040 | Who can I ask about it? | yes | seller-onboarding.md#5 | 1.000/0.333 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/1/1 |  |

Errors: 0
