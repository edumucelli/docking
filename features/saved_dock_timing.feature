@native_services
Feature: Saved dock timing values remain schedulable
  Scenario Outline: Oversized saved timings recover without blocking the dock
    Given a dock configuration with an oversized "<field>" value
    When the pointer leaves and returns using the real GTK scheduler
    Then the dock completes both its hide and reveal animations

    Examples:
      | field           |
      | hide_delay_ms   |
      | unhide_delay_ms |
      | hide_time_ms    |
