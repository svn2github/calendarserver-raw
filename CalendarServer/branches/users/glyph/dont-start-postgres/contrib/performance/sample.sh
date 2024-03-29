#!/bin/bash -x

. ./benchlib.sh

sudo -v # Force up to date sudo token before the user walks away

REV=$1
LOGS=$2
RESULTS=$3

update_and_build $REV

DATE="`./svn-committime $SOURCE $REV`"
for backend in $BACKENDS; do
  setbackend $backend
  pushd $SOURCE
  stop
  rm -rf data/
  start 2
  echo "instance pid files: ${instances[*]}"
  popd
  sudo ./run.sh ./benchmark --label r$REV-$backend --log-directory $LOGS $BENCHMARKS
  data=`echo -n r$REV-$backend*`
  ./run.sh ./massupload \
      --url $ADDURL --revision $REV \
      --revision-date "$DATE" --environment nmosbuilder \
      --backend $backend \
      --benchmarks "$BENCHMARKS" \
      --parameters "1 9 81" \
      --statistics "${STATISTICS[*]}" \
      $data
  mv $data $RESULTS
done
