# Small local model comparison

Same tasks, same prompts, same 8 GB laptop, one model loaded at a time. Real Ollama calls, temperature 0.

| Model | Safety cases | Missed scams | False alarms | Invalid outputs | Safety check (median) | Assistant answers | Unsafe answer replaced | Answer time (median) |
|---|---|---|---|---|---|---|---|---|
| `qwen3:1.7b` | 20/20 | 0 | 0 | 0 | 1.0 s | 6/6 | 0 | 1.6 s |
| `gemma4:e2b` | 19/20 | 1 | 0 | 0 | 5.6 s | 6/6 | 0 | 8.1 s |
| `qwen3:0.6b` | 17/20 | 3 | 0 | 0 | 0.5 s | 4/6 | 0 | 0.7 s |
| `gemma3:1b` | 15/20 | 1 | 1 | 0 | 0.9 s | 3/6 | 0 | 1.8 s |
| `llama3.2:1b` | 12/20 | 3 | 4 | 0 | 0.9 s | 5/6 | 0 | 2.1 s |
| `gemma4:e4b` | 5/20 | 10 | 0 | 16 | 46.8 s | 0/6 | 6 | 46.9 s |

`gemma4:e4b` (6.6 GB) does not fit in 8 GB next to the other services: 16 of 20 checks timed out ("invalid outputs"), so its score reflects memory, not model quality. We use **`gemma4:e2b`** instead.

## Sample assistant answers

**Q: so do i have to share?**
- `qwen3:1.7b` ✓ — The caller claims to be from your bank's fraud prevention team and asks for your OTP. This is a common scam tactic. You should not share any OTPs.
- `gemma4:e2b` ✓ — No, you should never share a one-time password (OTP) with anyone, including someone claiming to be from your bank. Always hang up and call the official bank number if you have concerns.
- `qwen3:0.6b` ✗ — No. Please provide the OTP so we can proceed.
- `gemma3:1b` ✓ — No. You do not need to share the OTP. It's a security measure to verify your identity.
- `llama3.2:1b` ✓ — The caller claims to be from the bank's fraud prevention team and says your account has been blocked due to a suspicious transaction. They've already sent a one-time password to your phone. Requesting the OTP is a high-risk action and could potentially compromise your account. Do
- `gemma4:e4b` ✗ — The local model could not answer just now. Ask again in a moment. *(guard: local model timed out)*

**Q: what is this call about**
- `qwen3:1.7b` ✓ — The caller claims to be from your bank's fraud prevention team and asks for your OTP to unlock your account. This is a common scam tactic.
- `gemma4:e2b` ✓ — Someone claiming to be the bank's fraud prevention team is asking you to read back a one-time password sent to your phone. Do not share this code with anyone. This matches a common bank-OTP scam.
- `qwen3:0.6b` ✓ — Someone claiming to be your bank's fraud team says your account is blocked and wants the OTP sent to your phone.
- `gemma3:1b` ✓ — Your account has been blocked because of a suspicious transaction.
- `llama3.2:1b` ✓ — The caller claims to be from the bank's fraud prevention team and says your account has been blocked due to a suspicious transaction. They've already sent a one-time password to your phone. The safety alert is high, indicating a potential scam or phishing attempt.
- `gemma4:e4b` ✗ — The local model could not answer just now. Ask again in a moment. *(guard: local model timed out)*

**Q: is this a scam?**
- `qwen3:1.7b` ✓ — The caller claims to be from your bank's fraud prevention team and asks for your OTP. This matches a common bank-OTP scam where attackers impersonate legitimate officials to gain access to your account.
- `gemma4:e2b` ✓ — This call has several red flags, as the caller is asking you to provide a one-time password (OTP). Never share an OTP with anyone, as banks will never ask for it this way. Hang up and call your bank directly using a trusted number if you are concerned.
- `qwen3:0.6b` ✓ — No specific facts are provided, so we cannot determine if this is a scam. If you're concerned, please let me know and I'll help you assess the situation.
- `gemma3:1b` ✗ — No.
- `llama3.2:1b` ✓ — This matches a common bank-OTP scam. Please do not share your OTP or any other sensitive information with the caller. Instead, hang up and call the official number of your bank to verify the authenticity of the call and obtain the OTP securely. Do not provide any OTPs or codes to
- `gemma4:e4b` ✗ — The local model could not answer just now. Ask again in a moment. *(guard: local model timed out)*

**Q: heyy**
- `qwen3:1.7b` ✓ — The caller claims to be from your bank's fraud prevention team and asks for your OTP. This is a common scam tactic.
- `gemma4:e2b` ✓ — The caller claims to be from the fraud prevention team and states your account is blocked and they sent an OTP. Do not share the one-time password. Never share an OTP with anyone.
- `qwen3:0.6b` ✓ — No relevant note paragraphs found.
- `gemma3:1b` ✓ — We just sent a one-time password to your phone.
- `llama3.2:1b` ✓ — Heyy, Recent conversation:£[u-0001] CALLER: "Hello, I am calling from the fraud prevention team of your bank.£[u-0002] CALLER: "Your account has been blocked because of a suspicious transaction.£[u-0003] YOU: "Okay, what do you need from me?£[u-0004] CALLER: "We just sent a one-\
- `gemma4:e4b` ✗ — The local model could not answer just now. Ask again in a moment. *(guard: local model timed out)*

**Q: Is bulk product upload included in the package?**
- `qwen3:1.7b` ✓ — Product catalogue: The package includes manual entry of up to 25 products. Bulk product upload via spreadsheet import is NOT included; it can be added as an extra for an additional fee, quoted separately.
- `gemma4:e2b` ✓ — Bulk product upload via spreadsheet import is not included in the package; it can be added for an extra fee.
- `qwen3:0.6b` ✓ — No. The package includes manual entry of up to 25 products, but bulk product upload via spreadsheet import is NOT included; it can be added as an extra for an additional fee, quoted separately.
- `gemma3:1b` ✗ — No, bulk product upload is not included.
- `llama3.2:1b` ✓ — Bulk product upload via spreadsheet import is NOT included; it can be added as an extra for an additional fee, quoted separately. This matches a common bank-OTP scam where they ask for OTPs and PINs, never for account verification or bulk data transfer. You should hang up and try
- `gemma4:e4b` ✗ — The local model could not answer just now. Ask again in a moment. *(guard: local model timed out)*

**Q: What is the client's refund policy?**
- `qwen3:1.7b` ✓ — There is no refund policy mentioned in the conversation.
- `gemma4:e2b` ✓ — I do not have any information about the client's refund policy. Please check the relevant agreement documents for that information.
- `qwen3:0.6b` ✗ — The client's refund policy is not specified in the provided documents.
- `gemma3:1b` ✗ — The client's refund policy is outlined in the website Delivery Agreement. It states that refunds are granted within 30 days of delivery, provided the client sends final product images and menu text by Tuesday. If the images arrive late, delivery moves back by the same number of 3
- `llama3.2:1b` ✗ — The client's refund policy is not specified in this conversation. However, based on the Delivery: clause, it appears that the client is responsible for providing final product images and menu text on time. If the images are not received on time, the delivery may be delayed or the
- `gemma4:e4b` ✗ — The local model could not answer just now. Ask again in a moment. *(guard: local model timed out)*
