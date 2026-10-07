Feature: Dock item animation under UI load
  Item insertion and removal should follow elapsed time and current geometry.

  Scenario: Delayed frames do not stretch animation or retain a removed item
    Given an applet insertion animation has started
    When item animation frames are delayed by 80 milliseconds
    Then the insertion completes progressively within two delayed frames
    When I remove the applet with the same frame delay
    Then the removal completes without stale painted geometry
