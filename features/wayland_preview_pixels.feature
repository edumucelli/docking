@native_services
Feature: Convert shared Wayland capture pixels without changing the preview
  Scenario Outline: Preserve the window's shape inside the requested bounds
    Given a wide "<kind>" Wayland capture requested within a square
    When the wide Wayland capture is decoded
    Then the preview keeps the wide window's aspect ratio

    Examples:
      | kind     |
      | wayland  |
      | hyprland |
      | phoc     |

  Scenario Outline: Alpha, orientation and row padding survive conversion
    Given a padded "<format>" Wayland capture with "<orientation>" rows
    When the Wayland preview pixels are decoded and capture storage is released
    Then preview colors and alpha match the original capture

    Examples:
      | format | orientation |
      | ARGB   | normal      |
      | ARGB   | inverted    |
      | XRGB   | normal      |
      | XRGB   | inverted    |
