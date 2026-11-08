// FreeBSD's action pins AnyVM v0.7.2, whose browser download can return 504.
// Keep the same release asset while using GitHub's release-asset API.
import https from "node:https";
import { syncBuiltinESMExports } from "node:module";

const browserUrl =
  "https://github.com/anyvm-org/anyvm/releases/download/v0.7.2/anyvm.py";
const assetUrl =
  "https://api.github.com/repos/anyvm-org/anyvm/releases/assets/589232152";
const originalGet = https.get;

https.get = (url, ...args) => {
  if (url !== browserUrl) {
    return originalGet(url, ...args);
  }
  const [options = {}, callback] =
    typeof args[0] === "function" ? [{}, args[0]] : args;
  return originalGet(
    assetUrl,
    {
      ...options,
      headers: {
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
