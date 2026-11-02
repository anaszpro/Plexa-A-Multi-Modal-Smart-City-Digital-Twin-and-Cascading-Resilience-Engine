export default function ResponsePlanner() {
  return (
    <section aria-labelledby="response-heading">
      <h1 id="response-heading">Response planner</h1>
      <p>Choose the starting point and destination for your response.</p>

      <div className="planner-fields">
        <div className="planner-field">
          <label htmlFor="response-origin">Starting point</label>
          <input
            id="response-origin"
            name="origin"
            type="text"
            placeholder="Enter an address or place"
            aria-describedby="location-help"
          />
        </div>

        <div className="planner-field">
          <label htmlFor="response-destination">Destination</label>
          <input
            id="response-destination"
            name="destination"
            type="text"
            placeholder="Enter an address or place"
            aria-describedby="location-help"
          />
        </div>

        <p id="location-help" className="field-help">
          Location search and map selection are not connected yet.
        </p>

        <button
          type="button"
          className="planner-submit"
          disabled
          aria-describedby="routing-status"
        >
          Compare routes
        </button>

        <p id="routing-status" className="field-help">
          Route comparison will become available when routing is connected.
        </p>
      </div>
    </section>
  );
}