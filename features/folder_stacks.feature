Feature: Folder stacks
  Folder stacks should open from a click and close when the user moves to a different dock item.

  Scenario: Clicking a folder opens its stack
    When I left click the "file:///tmp/docs" dock item
    Then the folder stack for "file:///tmp/docs" is open

  Scenario: Moving to another dock item closes an open folder stack
    Given the folder stack for "file:///tmp/docs" is open
    When I move the pointer to the "firefox.desktop" dock item
    Then no folder stack is open
