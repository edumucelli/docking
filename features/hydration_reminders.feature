Feature: Hydration reminders fire when the configured interval ends
  Scenario Outline: Each interval reaches its reminder on the final second
    Given a hydration reminder with an interval of <minutes> minutes
    When one second remains in the hydration interval
    Then the hydration reminder is not urgent
    When the hydration interval's final second passes
    Then the hydration reminder is urgent and asks me to drink water
    When I refill the hydration reminder
    Then the hydration reminder is not urgent

    Examples:
      | minutes |
      | 15      |
      | 30      |
      | 45      |
      | 60      |
      | 90      |
