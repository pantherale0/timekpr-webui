# Browser Restrictions

Guardian allows you to enforce browser-level safety policies directly on your child's web browsers. By using OS-level policy enforcement, these restrictions apply system-wide, preventing children from bypassing them or turning them off.

Currently, browser restrictions are supported on **Linux** client devices.

---

## Supported Browsers

Guardian applies browser restrictions to the following web browsers on Linux:
- **Google Chrome**
- **Chromium**
- **Brave Browser**

---

## Available Restrictions

You can configure the following safety settings in the parent dashboard under the child's profile settings:

### 1. Disable Incognito Mode
* **What it does:** Disables the browser's "Incognito" (private browsing) window option.
* **Why use it:** Ensures all web traffic, history, and active usage are visible to filtering and monitoring. The option to open an Incognito window is greyed out/unavailable in the browser menu.

### 2. Enforce Google SafeSearch
* **What it does:** Forces Google Search to filter out explicit content (such as pornography and graphic violence) from search results.
* **Why use it:** Provides an extra layer of search filtering directly at the search engine level. This setting cannot be toggled off by the user within Google's settings page.

### 3. YouTube Restricted Mode
* **What it does:** Restricts mature or potentially inappropriate video content on YouTube.
* **Available Levels:**
    * **Off:** No YouTube content restrictions.
    * **Moderate:** Filters out many mature videos while keeping most content accessible.
    * **Strict (Recommended):** Maximizes filtering of mature, violent, or age-restricted content.
* **Why use it:** Helps keep the YouTube feed age-appropriate without blocking the website entirely.

### 4. Block Other Extensions
* **What it does:** Blocks your child from installing any third-party browser extensions, except for the **Guardian monitor extension**.
* **Allowed Extension IDs:** You can specify additional, trusted extension IDs (comma or space separated) if your child needs them for school or utility purposes (e.g., Google Translate or password managers).
* **Why use it:** Prevents children from installing unauthorized VPN extensions, proxies, or ad blockers designed to bypass parental controls.

### 5. Disable Browser-Native Generative AI
* **What it does:** Blocks native AI features built directly into modern browsers.
* **Affected Features:** Disables features like foundational model settings, "Help Me Write" text generation, AI history search, AI tab organization, and AI theme creators.
* **Why use it:** Keeps the browsing experience focused and prevents unchecked interaction with browser-level Generative AI models.

---

## Technical Details

Browser restrictions are pushed to the client device using **Chrome Enterprise Policies**. 

1. When you save restrictions in the Web UI, the server prepares a secure policy payload.
2. The local Guardian agent retrieves the payload and writes it directly to the system-managed policy directories:
   * `/etc/opt/chrome/policies/managed/timekpr_youtube.json`
   * `/etc/chromium/policies/managed/timekpr_youtube.json`
   * `/etc/brave/policies/managed/timekpr_youtube.json`
3. Because these files are managed by the root system administrator, they take precedence over user settings and cannot be deleted or modified by a standard user account.

---

## Related Guides

- [Device restrictions](device-restrictions.md)
- [YouTube History Monitoring](youtube-history.md)
- [Web content filtering](../web-ui/web-filters.md)
