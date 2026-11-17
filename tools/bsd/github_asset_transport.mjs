// The BSD actions pin these AnyVM versions, whose browser downloads can return
// 504. Keep the same release assets while using GitHub's release-asset API.
import https from "node:https";
import { syncBuiltinESMExports } from "node:module";

const assetUrls = new Map([
  [
    "https://github.com/anyvm-org/anyvm/releases/download/v0.7.2/anyvm.py",
    "https://api.github.com/repos/anyvm-org/anyvm/releases/assets/589232152",
  ],
  [
    "https://github.com/anyvm-org/anyvm/releases/download/v0.7.4/anyvm.py",
    "https://api.github.com/repos/anyvm-org/anyvm/releases/assets/658797422",
  ],
  [
    "https://github.com/anyvm-org/anyvm/releases/download/v0.7.5/anyvm.py",
    "https://api.github.com/repos/anyvm-org/anyvm/releases/assets/662367953",
  ],
]);
const originalGet = https.get;

https.get = (url, ...args) => {
  const assetUrl = assetUrls.get(url);
  if (!assetUrl) {
    return originalGet(url, ...args);
  }
  const [options = {}, callback] =
    typeof args[0] === "function" ? [{}, args[0]] : args;
  const token = process.env.INPUT_TOKEN;
  return originalGet(
    assetUrl,
    {
      ...options,
      headers: {
        // Reuse the action's token to avoid shared runner-IP rate limits.
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
        Accept: "application/octet-stream",
        "User-Agent": "docking-bsd-ci",
      },
    },
    callback,
  );
};

// The action imports https as an ES module namespace.
syncBuiltinESMExports();
