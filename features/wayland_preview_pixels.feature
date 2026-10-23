@native_services
Feature: Convert shared Wayland capture pixels without changing the preview
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
