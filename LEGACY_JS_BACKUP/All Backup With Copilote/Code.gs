/* ==========================================================================
   ARAF INTELLIGENCE — SECURE GMAIL OTP RELAY BRIDGE (Code.gs)
   Architecture: Clean Transactional Email • Spam-Free Delivery Format
   ========================================================================== */

function doGet() {
  return buildJsonResponse({ success: true, service: "ARAF Gmail OTP Relay Bridge", status: "ACTIVE" });
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return buildJsonResponse({ success: false, error: "Empty request payload" });
    }

    const data = JSON.parse(e.postData.contents || "{}");
    const expectedSecret = PropertiesService.getScriptProperties().getProperty("GMAIL_BRIDGE_SECRET");

    if (!expectedSecret) {
      return buildJsonResponse({ success: false, error: "Bridge misconfigured: Secret missing" });
    }

    if (!timingSafeCompare(String(data.secret || ""), expectedSecret)) {
      return buildJsonResponse({ success: false, error: "Unauthorized access: Invalid bridge secret" });
    }

    const targetEmail = String(data.to || "").trim().toLowerCase();
    const otpCode = String(data.otp || "").trim();

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(targetEmail);
    if (!emailRegex || targetEmail.length > 120) {
      return buildJsonResponse({ success: false, error: "Invalid recipient email address" });
    }

    if (!/^\d{6}$/.test(otpCode)) {
      return buildJsonResponse({ success: false, error: "OTP must be exactly 6 numeric digits" });
    }

    const cache = CacheService.getScriptCache();
    const cdKey = "OTP_CD_" + Utilities.base64EncodeWebSafe(targetEmail).slice(0, 40);
    if (cache.get(cdKey)) {
      return buildJsonResponse({ success: false, error: "Rate limit: Wait 45s before resending OTP" });
    }

    const dayKey = "OTP_DAY_" + Utilities.formatDate(new Date(), "Asia/Kolkata", "yyyyMMdd") + "_" + Utilities.base64EncodeWebSafe(targetEmail).slice(0, 32);
    const dayCount = Number(cache.get(dayKey) || "0");
    if (dayCount >= 10) {
      return buildJsonResponse({ success: false, error: "Daily recipient OTP quota exceeded" });
    }

    if (MailApp.getRemainingDailyQuota() < 2) {
      return buildJsonResponse({ success: false, error: "Daily email quota exhausted" });
    }

   cache.put(cdKey, "1", 45);
    cache.put(dayKey, String(dayCount + 1), 21600);

    const safeOtp = sanitizeHtml(otpCode);
    const safeEmail = sanitizeHtml(targetEmail);

    const emailHtml = `
      <div style="font-family: Arial, sans-serif; padding: 20px; background-color: #ffffff; color: #0f172a; max-width: 480px; margin: auto; border: 1px solid #e2e8f0; border-radius: 12px;">
        <h3 style="color: #00d09c; margin-top: 0; font-size: 18px;">ARAF INTELLIGENCE APP</h3>
        <p style="font-size: 13px; color: #334155;">Hello Trader,</p>
        <p style="font-size: 13px; color: #334155;">Your secure verification OTP for <b>${safeEmail}</b> is:</p>
        
        <div style="background-color: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 14px; text-align: center; margin: 16px 0;">
          <span style="font-size: 28px; font-weight: 800; letter-spacing: 6px; color: #0f172a; font-family: monospace;">${safeOtp}</span>
        </div>
        
        <p style="font-size: 11px; color: #64748b; margin-top: 14px;">This code is valid for 10 minutes. Do not share this OTP with anyone.</p>
        
        <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 16px 0;">
        <div style="font-size: 10px; color: #94a3b8; text-align: center;">
          <span>ARAF INTELLIGENCE APP • Automated Risk &amp; Security Desk</span>
        </div>
      </div>
    `;

    const plainTextBody = `Hello Trader,\n\nYour 6-digit ARAF INTELLIGENCE APP verification OTP for ${targetEmail} is: ${otpCode}\n\nThis code is valid for 10 minutes. Do not share this OTP with anyone.\n\nARAF INTELLIGENCE APP`;

    MailApp.sendEmail({
      to: targetEmail,
      subject: `ARAF Verification Code: ${otpCode}`,
      name: "ARAF INTELLIGENCE APP",
      body: plainTextBody,
      htmlBody: emailHtml
    });

    return buildJsonResponse({ success: true, message: "Email dispatched successfully" });

 } catch (err) {
    Logger.log("OTP Bridge Error: " + err.message);
    return buildJsonResponse({ success: false, error: "Email dispatch failed" });
  }
}

function buildJsonResponse(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function sanitizeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function timingSafeCompare(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}
