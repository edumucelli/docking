Feature: Dock visibility
  The dock should react coherently to user pointer movement in autohide mode.

  Scenario: The dock hides after the pointer leaves and shows again on enter
    Given the dock is in autohide mode
    When I move the pointer onto the dock
    And I advance dock time by 64 milliseconds
    Then the dock is visible
    When I move the pointer off the dock
    And I advance dock time by 96 milliseconds
    Then the dock is hidden
    When I move the pointer onto the dock
    And I advance dock time by 64 milliseconds
    Then the dock is visible
