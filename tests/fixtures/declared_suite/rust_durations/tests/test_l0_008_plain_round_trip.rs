//! L0_008 (Rust): a fixture test that shares a crate with L0_007, for Rack's
//! batching tests. Cases mirror L0_units/vectors/L0_001_parse_duration.json.

use rust_durations::parse_duration;

#[test]
fn l0_008_plain_round_trip() {
    assert_eq!(parse_duration("90m"), 5400);
}
