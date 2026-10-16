@native_services
Feature: Hardware controls and alarm edits retain the user's latest intent
  Scenario: Rapid brightness scrolling applies the final requested level
    Given brightness is 50 percent with deferred hardware writes
    When I scroll brightness up five times before the hardware replies
    And all deferred hardware work finishes
    Then the hardware and brightness tooltip show 60 percent

  Scenario: A second power-profile choice replaces a pending choice
    Given the balanced power profile with deferred hardware writes
    When I choose performance and then power-saver before the hardware replies
    And all deferred hardware work finishes
    Then the selected power profile and tooltip show power-saver

  Scenario Outline: An old volume read cannot undo a new input
    Given an old volume poll is waiting to be displayed
    When I change the volume control with "<action>"
    And the old volume poll is displayed
    Then the volume control retains the new "<action>" state

    Examples:
      | action |
      | scroll |
      | mute   |

  Scenario: Enabling a past alarm waits for its next occurrence
    Given a disabled 07:00 alarm at 09:00 today
    When I enable the alarm and its scheduler runs
    Then the alarm stays quiet and is scheduled for 07:00 tomorrow

  Scenario: A fired alarm can be moved to later today
    Given a 07:00 alarm that already rang today at 09:00
    When I change the alarm to 10:00 today
    Then it is scheduled for 10:00 today
    When the alarm clock reaches 10:00 today
    Then the edited alarm rings

  Scenario: A rejected brightness change restores the displayed hardware level
    Given brightness is 50 percent and the hardware rejects writes
    When I scroll brightness up five times before the hardware replies
    And all deferred hardware work finishes
    Then the brightness tooltip returns to 50 percent
