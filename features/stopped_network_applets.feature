@native_services
Feature: Unloaded applets discard replies from their previous lifetime
  Scenario Outline: A late network reply cannot change an unloaded applet
    Given a "<applet>" network reply is waiting for the GTK thread
    When I unload that applet and save a newer preference selection
    And the old network reply is delivered
    Then the newer preferences remain saved and no further request starts

    Examples:
      | applet     |
      | weather    |
      | crypto     |
      | currencyfx |
      | news       |
      | reddit     |
