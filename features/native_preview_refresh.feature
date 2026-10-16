@native_services
Feature: Native window previews follow changing window contents
  Scenario Outline: A cached preview refreshes after the window changes color
    Given a "<kind>" native preview showing a red window
    When the native window turns green and two seconds pass
    Then the native preview refreshes to green pixels

    Examples:
      | kind     |
      | wayland  |
      | hyprland |
      | phoc     |
