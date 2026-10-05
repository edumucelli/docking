@gtk_stack
Feature: Accessible overflowing stacks
  Folder and applet fans stay bounded without discarding the displayed entries.

  Scenario Outline: Large stacks scroll to both ends and their action
    Given a large stack at the "<edge>" edge
    Then the stack popup fits its workarea
    When I scroll and activate the last stack entry
    Then the last stack entry action is called
    When I reopen the stack and activate its first entry
    Then the first stack entry action is called
    When I reopen the stack and activate its action chip
    Then the stack action chip callback is called

    Examples:
      | edge   |
      | bottom |
      | top    |
      | left   |
      | right  |

  Scenario: Reusing an overflowing popup restores the small fan
    Given a large stack at the "bottom" edge
    When I replace it with a short 48 pixel stack
    Then the stack popup fits its workarea
    And the short stack has no scrolling container

  Scenario Outline: A compositor can shrink the viewport after initial sizing
    Given a native stack popup at the "<edge>" edge
    When the compositor grants a 300 by 180 viewport
    Then the last stack entry remains reachable

    Examples:
      | edge   |
      | bottom |
      | top    |
      | left   |
      | right  |
