#!/bin/bash
set -e

URLNAME="Data-Engineering-Pilipinas"
TMP_PAST="past.json"
TMP_UPCOMING="upcoming.json"
FINAL_JSON="all_events.json"
FINAL_CSV="all_events.csv"

fetch_events () {
  TYPE=$1
  CURSOR=null
  RESULTS="[]"

  while :; do
    RESPONSE=$(curl -s https://api.meetup.com/gql \
      -H "Content-Type: application/json" \
      -d "{
        \"query\": \"query(\$cursor: String) {
          groupByUrlname(urlname: \\\"$URLNAME\\\") {
            $TYPE(input: {first: 20, after: \$cursor}) {
              pageInfo { hasNextPage endCursor }
              edges { node { id title dateTime going } }
            }
          }
        }\",
        \"variables\": {\"cursor\": $CURSOR}
      }")

    EDGES=$(echo "$RESPONSE" | jq '.data.groupByUrlname.'"$TYPE"'.edges')
    RESULTS=$(echo "$RESULTS + $EDGES" | jq -s 'add')

    HASNEXT=$(echo "$RESPONSE" | jq '.data.groupByUrlname.'"$TYPE"'.pageInfo.hasNextPage')
    CURSOR=$(echo "$RESPONSE" | jq '.data.groupByUrlname.'"$TYPE"'.pageInfo.endCursor')

    if [ "$HASNEXT" != "true" ]; then
      break
    fi
  done

  echo "$RESULTS"
}

echo "í´„ Fetching past events..."
fetch_events pastEvents > $TMP_PAST

echo "í´„ Fetching upcoming events..."
fetch_events upcomingEvents > $TMP_UPCOMING

echo "í´„ Combining and saving JSON..."
jq -s '
  add
  | to_entries
  | map({
      row: (.key+1),
      title: .value.node.title,
      dateTime: .value.node.dateTime,
      rsvp: .value.node.going
    })
' $TMP_PAST $TMP_UPCOMING > $FINAL_JSON

echo "í´„ Saving CSV..."
jq -r '
  map([.row, .title, .dateTime, .rsvp])
  | (["row","title","dateTime","rsvp"], .[])
  | @csv
' $FINAL_JSON > $FINAL_CSV

echo "âœ… Done!"
echo "   JSON saved to $FINAL_JSON"
echo "   CSV  saved to $FINAL_CSV"

