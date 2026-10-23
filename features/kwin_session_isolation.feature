@atspi_session
Feature: KWin accessibility session isolation
  The dock must use the accessibility bus selected by its own session.

  Scenario: Two concurrent sessions have separate window lists
    Given isolated accessibility sessions "Alpha" and "Beta"
    Then each dock lists only its own session's window

  Scenario: A missing accessibility bus becomes available later
    Given an isolated session whose accessibility service is unavailable
    Then its dock has no tracked windows and remains running
    When that session's accessibility service becomes available
    Then its dock tracks the session window

  Scenario: A restarted bus uses a new address
    Given an isolated session with a tracked accessibility window
    When its accessibility bus disappears
    Then its dock has no tracked windows and remains running
    When that session's accessibility service becomes available
    Then its dock tracks the session window
    And the accessibility bus address has changed

  Scenario: An explicit address works without the discovery service
    Given an isolated session with an explicit accessibility address but no discovery
    Then its dock tracks the session window
