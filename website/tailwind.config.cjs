module.exports = {
  "content": [
    "./index.html",
    "./app.js"
  ],
  "theme": {
    "extend": {
      "colors": {
        "midnight": "#101827",
        "accent": "#ef4444",
        "skyline": "#3b82f6",
        "ember": "#f59e0b"
      },
      "boxShadow": {
        "glow": "0 0 0 1px rgba(255,255,255,0.07), 0 30px 90px rgba(15,23,42,0.22), 0 16px 52px rgba(245,158,11,0.16)",
        "soft": "0 24px 70px rgba(15,23,42,0.2)"
      },
      "keyframes": {
        "drift": {
          "0%, 100%": {
            "transform": "translate3d(0, 0, 0)"
          },
          "50%": {
            "transform": "translate3d(0, -14px, 0)"
          }
        },
        "pulseGlow": {
          "0%, 100%": {
            "opacity": "0.34",
            "transform": "scale(1)"
          },
          "50%": {
            "opacity": "0.62",
            "transform": "scale(1.05)"
          }
        },
        "marquee": {
          "0%": {
            "transform": "translateX(0)"
          },
          "100%": {
            "transform": "translateX(-50%)"
          }
        }
      },
      "animation": {
        "drift": "drift 8s ease-in-out infinite",
        "pulseGlow": "pulseGlow 7s ease-in-out infinite",
        "marquee": "marquee 24s linear infinite"
      }
    }
  }
};
