@native_services
Feature: Cinnamon dock surfaces leave transparent space interactive
  Scenario: Pointer events pass through transparent dock space
    Given a Cinnamon dock surface above another GTK window
    When the pointer moves outside the active dock region
    Then the window beneath receives the pointer motion
    When the pointer moves inside the active dock region
    Then the dock surface receives the pointer motion
