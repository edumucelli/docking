Feature: Slow system sensors do not block dock callbacks
  Scenario: Polling returns before sensors complete
    Given a system monitor with deferred sensor sampling
    When the system monitor is polled ten times
    Then one sensor request remains and no sensor was read on GTK
    When the sensor worker completes and GTK receives the result
    Then the system monitor tooltip contains the new sensor values

  Scenario: Stop and restart rejects an old result
    Given a system monitor with deferred sensor sampling
    When the system monitor is polled ten times
    And the system monitor is stopped and restarted
    And the sensor worker completes and GTK receives the result
    Then the old system monitor sensor result is ignored
    When the system monitor is polled ten times
    And the sensor worker completes and GTK receives the result
    Then the system monitor tooltip contains the new sensor values
