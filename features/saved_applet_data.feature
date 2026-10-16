@native_services
Feature: Saved applet data recovers from malformed records
  Scenario Outline: Invalid saved values do not prevent startup
    Given a saved "<applet>" configuration containing malformed values
    When the configured applet starts
    Then its icon and tooltip are available and valid saved data is retained

    Examples:
      | applet    |
      | hydration |
      | weather   |
      | sunrise   |
      | aiusage   |
      | certwatch |
