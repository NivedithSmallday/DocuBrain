import { ThreeDotsLoader } from "@/components/Loading";
import { getDatesList, useDocubrainBotAnalytics } from "../lib";
import { DateRangePickerValue } from "@/components/dateRangeSelectors/AdminDateRangeSelector";
import { Text } from "@opal/components";
import Title from "@/components/ui/title";
import CardSection from "@/components/admin/CardSection";
import { AreaChartDisplay } from "@/components/ui/areaChart";

export function DocubrainBotChart({
  timeRange,
}: {
  timeRange: DateRangePickerValue;
}) {
  const {
    data: docubrainBotAnalyticsData,
    isLoading: isDocubrainBotAnalyticsLoading,
    error: docubrainBotAnalyticsError,
  } = useDocubrainBotAnalytics(timeRange);

  let chart;
  if (isDocubrainBotAnalyticsLoading) {
    chart = (
      <div className="h-80 flex flex-col">
        <ThreeDotsLoader />
      </div>
    );
  } else if (
    !docubrainBotAnalyticsData ||
    docubrainBotAnalyticsData[0] == undefined ||
    docubrainBotAnalyticsError
  ) {
    chart = (
      <div className="h-80 text-red-600 text-bold flex flex-col">
        <p className="m-auto">Failed to fetch feedback data...</p>
      </div>
    );
  } else {
    const initialDate =
      timeRange.from || new Date(docubrainBotAnalyticsData[0].date);
    const dateRange = getDatesList(initialDate);

    const dateToDocubrainBotAnalytics = new Map(
      docubrainBotAnalyticsData.map((docubrainBotAnalyticsEntry) => [
        docubrainBotAnalyticsEntry.date,
        docubrainBotAnalyticsEntry,
      ])
    );

    chart = (
      <AreaChartDisplay
        className="mt-4"
        data={dateRange.map((dateStr) => {
          const docubrainBotAnalyticsForDate = dateToDocubrainBotAnalytics.get(dateStr);
          return {
            Day: dateStr,
            "Total Queries": docubrainBotAnalyticsForDate?.total_queries || 0,
            "Automatically Resolved":
              docubrainBotAnalyticsForDate?.auto_resolved || 0,
          };
        })}
        categories={["Total Queries", "Automatically Resolved"]}
        index="Day"
        colors={["indigo", "fuchsia"]}
        yAxisWidth={60}
      />
    );
  }

  return (
    <CardSection className="mt-8">
      <Title>Slack Channel</Title>
      <Text as="p">Total Queries vs Auto Resolved</Text>
      {chart}
    </CardSection>
  );
}
